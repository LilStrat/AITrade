"""Backtesting engine.

Runs the SAME ``TradingCore`` and ``LocalPaperBroker`` used for paper
trading, against an in-memory database and historical bars. So signals,
risk checks, order handling, fill simulation and accounting are identical
to paper trading; only the clock is simulated.

Known limitations (reported with every result):
* daily bars only - intraday paths are approximated (see execution.py)
* the instrument list is today's survivors -> survivorship bias
* split-adjusted prices; dividends credited as cash on the ex-date
* spreads/slippage are fixed estimates, not observed quotes
"""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from ..brokers.paper import LocalPaperBroker
from ..core.dataview import FrameDataView
from ..core.trading import TradingCore
from ..costs import CostModel
from ..db import Database
from ..notifications import NullAlerts
from ..portfolio.ledger import Ledger
from ..risk.manager import RiskManager
from ..strategies import REGISTRY
from . import metrics as M

LIMITATIONS = [
    "Simulated results on historical data. They do not predict future returns.",
    "Daily bars only: the order of intraday highs and lows is unknown; stops are assumed to trigger before targets.",
    "Survivorship bias: the instrument list contains today's large companies; delisted or shrunk companies are missing, which flatters results.",
    "Spreads and slippage are fixed estimates; real costs vary with liquidity and volatility.",
    "Prices are split-adjusted; dividends are credited as cash on the ex-date (pay dates are later in reality).",
    "Free data sources can contain errors; validation catches only some of them.",
]


@dataclass
class BacktestRequest:
    strategies: list[str]
    symbols: list[str]
    start: date
    end: date
    starting_capital: float = 10_000.0
    cost_profile: str | None = None
    params: dict[str, dict] = field(default_factory=dict)
    benchmark: str | None = None
    label: str = ""
    drawdown_auto_resume_sessions: int = 20
    settings_overrides: dict = field(default_factory=dict)


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def run_backtest(settings: dict, data: FrameDataView, req: BacktestRequest) -> dict:
    t0 = time.time()
    s = _merge(settings, req.settings_overrides)
    benchmark = req.benchmark or s["universe"]["benchmark"]
    db = Database(":memory:")
    ledger = Ledger(db)
    profile = req.cost_profile or s["paper"]["cost_profile"]
    acct = ledger.create_account("backtest", req.label or "backtest", req.starting_capital, profile, on=req.start)
    costs = CostModel(s, profile)
    broker = LocalPaperBroker(db, acct, costs, s)
    strategies = [REGISTRY[k](req.params.get(k, {})) for k in req.strategies]
    alerts = NullAlerts()
    core = TradingCore(db, s, data, broker, strategies, RiskManager(s, costs), alerts, mode="backtest",
                       benchmark=benchmark, apply_splits=False, record_signals=False,
                       auto_resume_drawdown_after=req.drawdown_auto_resume_sessions)
    symbols = [x for x in req.symbols if not data.history(x).empty]
    core.prepare(symbols, from_day=req.start)

    bench = data.history(benchmark)
    days = [d for d in bench.index if req.start <= d <= req.end]
    if not days:
        raise ValueError("no benchmark data in the requested period")
    signals_total = signals_approved = 0
    rejections: dict[str, int] = {}
    for d in days:
        core.process_session(d)
        if d == days[-1]:
            break  # no point deciding on the last day
        res = core.decide(d)
        for sg in res.signals:
            signals_total += 1
            if sg["status"] == "ordered":
                signals_approved += 1
            for rj in sg["risk"]["rejections"]:
                key = rj.split(" (")[0][:60]
                rejections[key] = rejections.get(key, 0) + 1

    snaps = pd.DataFrame([dict(r) for r in db.query(
        "SELECT * FROM equity_snapshots WHERE account_id=? ORDER BY date", (acct,))])
    snaps["date"] = pd.to_datetime(snaps["date"])
    equity = snaps.set_index("date")["equity_gbp"]
    trades = extract_trades(db, acct, days)
    tm = M.trade_metrics(trades)
    fills = [dict(r) for r in db.query("SELECT * FROM fills WHERE account_id=?", (acct,))]
    total_costs = sum(f["commission_gbp"] + f["stamp_duty_gbp"] + f["other_fees_gbp"] + f["fx_cost_gbp"]
                      + f["spread_slippage_gbp"] for f in fills)
    cost_cum = _cumulative_costs(fills, equity.index)
    equity_before_costs = equity + cost_cum
    state = ledger.state(acct)
    em = M.equity_metrics(equity, float(s["universe"]["cash_benchmark_rate_pct"]))
    em_gross = M.equity_metrics(equity_before_costs, float(s["universe"]["cash_benchmark_rate_pct"]))
    bh = buy_and_hold(data, benchmark, days, req.starting_capital, costs)
    bhm = M.equity_metrics(bh, float(s["universe"]["cash_benchmark_rate_pct"])) if not bh.empty else {}
    cash_eq = cash_benchmark(equity.index, req.starting_capital, float(s["universe"]["cash_benchmark_rate_pct"]))
    result = {
        "request": {**req.__dict__, "start": req.start.isoformat(), "end": req.end.isoformat()},
        "data_source_note": getattr(data, "source_note", ""),
        "period": {"start": days[0].isoformat(), "end": days[-1].isoformat(), "sessions": len(days)},
        "metrics_after_costs": em,
        "metrics_before_costs_approx": em_gross,
        "trade_metrics": tm,
        "exposure_pct": M.exposure_pct(snaps.set_index("date")["positions_gbp"], equity),
        "costs": {
            "total_gbp": total_costs,
            "pct_of_starting_capital": total_costs / req.starting_capital * 100,
            "commission_gbp": sum(f["commission_gbp"] for f in fills),
            "stamp_duty_gbp": sum(f["stamp_duty_gbp"] for f in fills),
            "other_fees_gbp": sum(f["other_fees_gbp"] for f in fills),
            "fx_gbp": sum(f["fx_cost_gbp"] for f in fills),
            "spread_slippage_gbp": sum(f["spread_slippage_gbp"] for f in fills),
            "profile": costs.label,
        },
        "dividends_gbp": state.dividends_gbp,
        "benchmark": {"symbol": benchmark, "metrics": bhm},
        "cash_benchmark": {"rate_pct": s["universe"]["cash_benchmark_rate_pct"],
                           "end_equity": float(cash_eq.iloc[-1]) if len(cash_eq) else req.starting_capital},
        "signals": {"total": signals_total, "ordered": signals_approved, "top_rejections":
                    sorted(rejections.items(), key=lambda kv: -kv[1])[:8]},
        "risk_events": {"drawdown_pauses": core.drawdown_pauses, "daily_loss_pauses": core.daily_loss_pauses,
                        "note": f"In backtests a drawdown pause auto-resumes after {req.drawdown_auto_resume_sessions} sessions; in paper trading you must resume it."},
        "trades": trades,
        "equity_curve": [[d.strftime("%Y-%m-%d"), round(v, 2)] for d, v in equity.items()],
        "benchmark_curve": [[d.strftime("%Y-%m-%d"), round(v, 2)] for d, v in bh.items()],
        "cash_curve": [[d.strftime("%Y-%m-%d"), round(v, 2)] for d, v in cash_eq.items()],
        "limitations": LIMITATIONS,
        "runtime_seconds": round(time.time() - t0, 2),
    }
    db.close()
    return result


def _cumulative_costs(fills: list[dict], index: pd.DatetimeIndex) -> pd.Series:
    if not fills:
        return pd.Series(0.0, index=index)
    f = pd.DataFrame(fills)
    f["c"] = f["commission_gbp"] + f["stamp_duty_gbp"] + f["other_fees_gbp"] + f["fx_cost_gbp"] + f["spread_slippage_gbp"]
    daily = f.groupby(pd.to_datetime(f["trade_date"]))["c"].sum()
    return daily.reindex(index, fill_value=0.0).cumsum()


def extract_trades(db: Database, account_id: int, days: list[date]) -> list[dict]:
    """Round-trip trades from the fills (FIFO per symbol entry)."""
    day_pos = {d.isoformat(): i for i, d in enumerate(days)}
    rows = db.query("""SELECT f.*, o.purpose, o.strategy, o.reason FROM fills f JOIN orders o ON o.id=f.order_id
                       WHERE f.account_id=? ORDER BY f.trade_date, f.rowid""", (account_id,))
    open_by_sym: dict[str, dict] = {}
    out: list[dict] = []
    for r in rows:
        r = dict(r)
        fees = r["commission_gbp"] + r["stamp_duty_gbp"] + r["other_fees_gbp"] + r["fx_cost_gbp"]
        if r["side"] == "BUY":
            t = open_by_sym.get(r["symbol"])
            if t is None:
                t = {"symbol": r["symbol"], "strategy": r["strategy"], "entry_date": r["trade_date"], "qty": 0.0,
                     "buy_gross": 0.0, "buy_ref": 0.0, "buy_fees": 0.0, "sell_gross": 0.0, "sell_ref": 0.0,
                     "sell_fees": 0.0, "sold": 0.0, "entry_reason": r["reason"], "impact": 0.0}
                open_by_sym[r["symbol"]] = t
            t["qty"] += r["quantity"]
            t["buy_gross"] += r["gross_gbp"]
            t["buy_ref"] += r["gross_gbp"] - r["spread_slippage_gbp"]
            t["buy_fees"] += fees
            t["impact"] += r["spread_slippage_gbp"]
            t["entry_price"] = r["price"]
        else:
            t = open_by_sym.get(r["symbol"])
            if t is None:
                continue
            t["sold"] += r["quantity"]
            t["sell_gross"] += r["gross_gbp"]
            t["sell_ref"] += r["gross_gbp"] + r["spread_slippage_gbp"]
            t["sell_fees"] += fees
            t["impact"] += r["spread_slippage_gbp"]
            if t["sold"] >= t["qty"] - 1e-9:
                net = t["sell_gross"] - t["sell_fees"] - t["buy_gross"] - t["buy_fees"]
                out.append({
                    "symbol": t["symbol"], "strategy": t["strategy"], "entry_date": t["entry_date"],
                    "exit_date": r["trade_date"], "quantity": t["qty"], "entry_price": t["entry_price"],
                    "exit_price": r["price"], "net_pnl_gbp": round(net, 2),
                    "gross_pnl_gbp": round(t["sell_ref"] - t["buy_ref"], 2),
                    "costs_gbp": round(t["buy_fees"] + t["sell_fees"] + t["impact"], 2),
                    "return_pct": round(net / (t["buy_gross"] + t["buy_fees"]) * 100, 2),
                    "holding_sessions": day_pos.get(r["trade_date"], 0) - day_pos.get(t["entry_date"], 0),
                    "exit_purpose": r["purpose"], "exit_reason": r["reason"], "exit_note": r["execution_note"],
                    "entry_reason": t["entry_reason"]})
                del open_by_sym[r["symbol"]]
    for t in open_by_sym.values():
        out.append({"symbol": t["symbol"], "strategy": t["strategy"], "entry_date": t["entry_date"], "exit_date": None,
                    "quantity": t["qty"], "entry_price": t["entry_price"], "status": "open at end of test"})
    return out


def buy_and_hold(data: FrameDataView, symbol: str, days: list[date], capital: float, costs: CostModel) -> pd.Series:
    """Buy the benchmark at the first session's open (with costs), hold, reinvest nothing,
    credit dividends as cash."""
    h = data.history(symbol)
    h = h[(h.index >= days[0]) & (h.index <= days[-1])]
    if h.empty:
        return pd.Series(dtype=float)
    ins = data.instrument(symbol)
    price = float(h["open"].iloc[0]) * (1 + costs.market_impact_bps(ins) / 10_000)
    qty = int(capital * 0.99 // price)
    fees = costs.fees("BUY", qty, price, "GBP", 1.0, ins).total_gbp
    cash = capital - qty * price - fees
    divs = {a["date"]: a["value"] for a in data._actions.get(symbol, []) if a["type"] == "dividend"}
    vals = []
    for d, r in h.iterrows():
        if d in divs and d != days[0]:
            cash += qty * divs[d]
        vals.append(cash + qty * float(r["close"]))
    return pd.Series(vals, index=pd.to_datetime(list(h.index)))


def cash_benchmark(index: pd.DatetimeIndex, capital: float, rate_pct: float) -> pd.Series:
    if len(index) == 0:
        return pd.Series(dtype=float)
    t = (index - index[0]).days / 365.25
    return pd.Series(capital * (1 + rate_pct / 100) ** t, index=index)
