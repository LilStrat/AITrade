"""Walk-forward research with a final untouched holdout period.

Procedure (fixed in advance - do not change it after looking at results):

  |---------------- development (first 75%) ----------------|-- holdout (last 25%) --|
   rolling windows: [train 3y][test 1y] -> step 1y ...          run ONCE at the end

1. Within development, for each rolling window, every parameter set in a
   small pre-registered grid is backtested on the TRAIN years; the best (by
   return / drawdown, minimum trade count) is then run on the following
   TEST year it has never seen. Chaining those test years gives a
   walk-forward out-of-sample (OOS) record.
2. A sensitivity table shows every grid point over the whole development
   period, so you can see whether results depend on a lucky parameter.
3. The holdout period is run exactly once with the default parameters and
   once with the parameters chosen on development data.
4. A verdict is assigned using the PRE-REGISTERED criteria below.

Repeatedly re-running this after tweaking strategies turns the holdout
into in-sample data. The report records how many research runs exist.
"""

from __future__ import annotations

import itertools
import logging
import os
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta

import pandas as pd

from .engine import BacktestRequest, run_backtest

log = logging.getLogger(__name__)

GRIDS: dict[str, dict[str, list]] = {
    "trend_pullback": {"rsi_entry": [5, 10, 15], "stop_atr": [2.0, 3.0], "max_hold": [5, 10]},
    "breakout_momentum": {"breakout_len": [20, 55, 100], "trail_atr": [2.5, 3.5], "volume_mult": [1.2, 1.8]},
}

CRITERIA = {
    "min_holdout_trades": 20,
    "min_profit_factor": 1.10,
    "must_beat_cash": True,
    "min_walk_forward_oos_return_pct": 0.0,
}

VERDICT_TEXT = {
    "REJECT": "REJECT - the evidence does not support trading this strategy on this data after costs.",
    "INSUFFICIENT EVIDENCE": "INSUFFICIENT EVIDENCE - too few trades to judge. Do not treat any result as meaningful.",
    "PROVISIONAL": ("PROVISIONAL - passed the pre-registered tests on unseen data. This is NOT proof of future profit; "
                    "it only qualifies the strategy for extended paper trading."),
}


def grid_points(strategy: str) -> list[dict]:
    g = GRIDS[strategy]
    keys = list(g)
    return [dict(zip(keys, combo)) for combo in itertools.product(*(g[k] for k in keys))]


def _objective(res: dict) -> float:
    tm, em = res["trade_metrics"], res["metrics_after_costs"]
    if tm.get("trades", 0) < 10:
        return float("-inf")
    return em["total_return_pct"] / max(em["max_drawdown_pct"], 1.0)


def _run(args) -> dict:
    settings, data, req = args
    return run_backtest(settings, data, req)


def _map(fn, jobs, workers: int):
    if workers <= 1 or len(jobs) <= 1:
        return [fn(j) for j in jobs]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(fn, jobs))


def _summary(res: dict) -> dict:
    em, tm = res["metrics_after_costs"], res["trade_metrics"]
    return {
        "total_return_pct": em.get("total_return_pct"), "annualised_return_pct": em.get("annualised_return_pct"),
        "max_drawdown_pct": em.get("max_drawdown_pct"), "sharpe": em.get("sharpe"),
        "trades": tm.get("trades", 0), "win_rate_pct": tm.get("win_rate_pct"),
        "profit_factor": tm.get("profit_factor"), "costs_gbp": res["costs"]["total_gbp"],
        "gross_before_costs_return_pct": res["metrics_before_costs_approx"].get("total_return_pct"),
        "benchmark_return_pct": res["benchmark"]["metrics"].get("total_return_pct"),
        "benchmark_max_dd_pct": res["benchmark"]["metrics"].get("max_drawdown_pct"),
        "cash_return_pct": (res["cash_benchmark"]["end_equity"] / res["metrics_after_costs"].get("start_equity", 1) - 1) * 100
        if res["metrics_after_costs"].get("start_equity") else None,
        "exposure_pct": res["exposure_pct"],
    }


def run_research(settings: dict, data, strategy: str, symbols: list[str], start: date, end: date,
                 capital: float = 10_000.0, workers: int | None = None, train_years: int = 3,
                 test_years: int = 1, progress=None) -> dict:
    workers = workers if workers is not None else max(1, min(4, (os.cpu_count() or 2) - 1))
    span = (end - start).days
    if span < 365 * (train_years + test_years + 1):
        raise ValueError(f"need at least {train_years + test_years + 1} years of data after warm-up")
    holdout_start = start + timedelta(days=int(span * 0.75))
    dev_end = holdout_start - timedelta(days=1)
    grid = grid_points(strategy)
    notes = []

    def req(p, s, e, label):
        return BacktestRequest(strategies=[strategy], symbols=symbols, start=s, end=e, starting_capital=capital,
                               params={strategy: p}, label=label)

    # ---- 1. rolling walk-forward inside development
    windows = []
    w_start = start
    while True:
        tr_end = w_start + timedelta(days=int(365.25 * train_years)) - timedelta(days=1)
        te_end = tr_end + timedelta(days=int(365.25 * test_years))
        if te_end > dev_end:
            break
        windows.append((w_start, tr_end, tr_end + timedelta(days=1), te_end))
        w_start = w_start + timedelta(days=int(365.25 * test_years))
    wf = []
    oos_growth = 1.0
    for n, (a, b, c, d) in enumerate(windows):
        if progress:
            progress(f"walk-forward window {n + 1}/{len(windows)}: training {a}..{b}")
        train = _map(_run, [(settings, data, req(p, a, b, "train")) for p in grid], workers)
        scores = [_objective(r) for r in train]
        best_i = max(range(len(grid)), key=lambda i: scores[i])
        chosen = grid[best_i] if scores[best_i] != float("-inf") else {}
        if not chosen:
            notes.append(f"Window {a}..{b}: no grid point had 10+ trades; defaults used out-of-sample.")
        test = run_backtest(settings, data, req(chosen, c, d, "oos"))
        r = test["metrics_after_costs"]["total_return_pct"]
        oos_growth *= 1 + r / 100
        wf.append({"train": [a.isoformat(), b.isoformat()], "test": [c.isoformat(), d.isoformat()],
                   "chosen_params": chosen, "train_score": scores[best_i], "oos": _summary(test)})

    # ---- 2. sensitivity over the whole development period
    if progress:
        progress("sensitivity grid over the development period")
    dev = _map(_run, [(settings, data, req(p, start, dev_end, "dev")) for p in grid], workers)
    sens = [{"params": p, **_summary(r)} for p, r in zip(grid, dev)]
    dev_scores = [_objective(r) for r in dev]
    best_dev = grid[max(range(len(grid)), key=lambda i: dev_scores[i])] if dev_scores and max(dev_scores) != float("-inf") else {}

    # ---- 3. holdout, run once each
    if progress:
        progress(f"holdout test {holdout_start}..{end} (run once)")
    hold_default = run_backtest(settings, data, req({}, holdout_start, end, "holdout-default"))
    hold_chosen = run_backtest(settings, data, req(best_dev, holdout_start, end, "holdout-chosen")) if best_dev else hold_default

    # ---- 4. verdict (pre-registered)
    hs = _summary(hold_chosen)
    wf_oos_pct = (oos_growth - 1) * 100
    reasons = []
    if hs["trades"] < CRITERIA["min_holdout_trades"]:
        verdict = "INSUFFICIENT EVIDENCE"
        reasons.append(f"only {hs['trades']} holdout trades (need {CRITERIA['min_holdout_trades']})")
    else:
        verdict = "PROVISIONAL"
        pf = hs["profit_factor"] or 0
        if pf < CRITERIA["min_profit_factor"]:
            verdict = "REJECT"
            reasons.append(f"holdout profit factor {pf:.2f} < {CRITERIA['min_profit_factor']}")
        if CRITERIA["must_beat_cash"] and hs["cash_return_pct"] is not None and hs["total_return_pct"] <= hs["cash_return_pct"]:
            verdict = "REJECT"
            reasons.append(f"holdout return {hs['total_return_pct']:.2f}% did not beat cash {hs['cash_return_pct']:.2f}%")
        if windows and wf_oos_pct <= CRITERIA["min_walk_forward_oos_return_pct"]:
            verdict = "REJECT"
            reasons.append(f"walk-forward out-of-sample return {wf_oos_pct:.2f}% <= 0")
    if hs["benchmark_return_pct"] is not None and hs["total_return_pct"] < hs["benchmark_return_pct"]:
        notes.append(f"Holdout return {hs['total_return_pct']:.2f}% was below simply holding the benchmark "
                     f"({hs['benchmark_return_pct']:.2f}%). Lower exposure/drawdown may or may not justify that.")
    notes.append(f"{len(grid)} parameter sets were tested; the more variants tried, the more likely the best one "
                 "looks good by chance (multiple-testing bias).")
    return {
        "strategy": strategy, "symbols": symbols, "period": [start.isoformat(), end.isoformat()],
        "development": [start.isoformat(), dev_end.isoformat()], "holdout": [holdout_start.isoformat(), end.isoformat()],
        "criteria": CRITERIA, "grid": GRIDS[strategy],
        "walk_forward": wf, "walk_forward_oos_return_pct": wf_oos_pct,
        "sensitivity": sens, "chosen_params": best_dev,
        "holdout_default": _summary(hold_default), "holdout_chosen": hs,
        "holdout_chosen_full": {k: hold_chosen[k] for k in ("equity_curve", "benchmark_curve", "cash_curve", "costs",
                                                              "trade_metrics", "metrics_after_costs", "limitations")},
        "verdict": verdict, "verdict_text": VERDICT_TEXT[verdict], "verdict_reasons": reasons, "notes": notes,
    }


def research_markdown(r: dict, data_note: str) -> str:
    def f(v, nd=2, suffix=""):
        return "n/a" if v is None else (f"{v:.{nd}f}{suffix}" if isinstance(v, (int, float)) else str(v))

    lines = [f"# Research report: {r['strategy']}", "",
             f"**Verdict: {r['verdict']}** - {r['verdict_text']}", ""]
    for x in r["verdict_reasons"]:
        lines.append(f"- {x}")
    lines += ["", f"Data: {data_note}", f"Instruments: {len(r['symbols'])}", f"Development: {r['development'][0]} to {r['development'][1]}",
              f"Holdout (unseen): {r['holdout'][0]} to {r['holdout'][1]}", "",
              "## Holdout results (after estimated costs)", "",
              "| | Return % | Max DD % | Sharpe | Trades | Win % | Profit factor | Costs £ | Before-costs return % |",
              "|---|---|---|---|---|---|---|---|---|"]
    for name, h in (("Default params", r["holdout_default"]), ("Chosen params", r["holdout_chosen"])):
        lines.append(f"| {name} | {f(h['total_return_pct'])} | {f(h['max_drawdown_pct'])} | {f(h['sharpe'])} | {h['trades']} | "
                     f"{f(h['win_rate_pct'], 1)} | {f(h['profit_factor'])} | {f(h['costs_gbp'])} | {f(h['gross_before_costs_return_pct'])} |")
    h = r["holdout_chosen"]
    lines += ["", f"Benchmark buy-and-hold: {f(h['benchmark_return_pct'])}% (max DD {f(h['benchmark_max_dd_pct'])}%); "
              f"cash: {f(h['cash_return_pct'])}%", "",
              f"## Walk-forward out-of-sample (chained): {f(r['walk_forward_oos_return_pct'])}%", "",
              "| Train | Test | Chosen params | OOS return % | OOS trades |", "|---|---|---|---|---|"]
    for w in r["walk_forward"]:
        lines.append(f"| {w['train'][0]}..{w['train'][1]} | {w['test'][0]}..{w['test'][1]} | {w['chosen_params'] or 'defaults'} | "
                     f"{f(w['oos']['total_return_pct'])} | {w['oos']['trades']} |")
    lines += ["", "## Parameter sensitivity (development period)", "",
              "| Params | Return % | Max DD % | Trades | Profit factor |", "|---|---|---|---|---|"]
    for sres in r["sensitivity"]:
        lines.append(f"| {sres['params']} | {f(sres['total_return_pct'])} | {f(sres['max_drawdown_pct'])} | {sres['trades']} | {f(sres['profit_factor'])} |")
    lines += ["", "## Notes and limitations", ""] + [f"- {n}" for n in r["notes"] + r["holdout_chosen_full"]["limitations"]]
    return "\n".join(lines) + "\n"
