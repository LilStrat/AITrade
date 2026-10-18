"""Daily and weekly reports (Markdown, saved under exports/reports).

Every number comes from the database. Realised and unrealised results are
kept separate. Optional AI commentary is appended in a clearly labelled
section and never replaces the numbers.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from .. import paths
from ..db import Database
from ..portfolio.ledger import Ledger


def _money(v) -> str:
    if v is None:
        return "n/a"
    return f"-£{abs(v):,.2f}" if v < 0 else f"£{v:,.2f}"


def _snap(db: Database, aid: int, on_or_before: date) -> dict | None:
    r = db.one("SELECT * FROM equity_snapshots WHERE account_id=? AND date<=? ORDER BY date DESC LIMIT 1",
               (aid, on_or_before.isoformat()))
    return dict(r) if r else None


def build_report_data(db: Database, aid: int, start: date, end: date, benchmark: str, market=None) -> dict:
    ledger = Ledger(db)
    st = ledger.state(aid)
    s_end = _snap(db, aid, end)
    s_start = _snap(db, aid, start - timedelta(days=1)) or db.one(
        "SELECT * FROM equity_snapshots WHERE account_id=? ORDER BY date LIMIT 1", (aid,))
    s_start = dict(s_start) if s_start else None
    acc = dict(db.one("SELECT * FROM accounts WHERE id=?", (aid,)))
    fills = [dict(r) for r in db.query("""SELECT f.*, o.purpose, o.strategy, o.reason FROM fills f JOIN orders o ON o.id=f.order_id
                                          WHERE f.account_id=? AND f.trade_date BETWEEN ? AND ? ORDER BY f.trade_date""",
                                       (aid, start.isoformat(), end.isoformat()))]
    costs = sum(f["commission_gbp"] + f["stamp_duty_gbp"] + f["other_fees_gbp"] + f["fx_cost_gbp"] for f in fills)
    impact = sum(f["spread_slippage_gbp"] for f in fills)
    # realised P&L in the period = change in cumulative realised P&L
    realised_period = 0.0
    if s_end and s_start:
        realised_period = s_end["realised_pnl_gbp"] - s_start["realised_pnl_gbp"]
    signals = [dict(r) for r in db.query("SELECT * FROM signals WHERE account_id=? AND as_of_date BETWEEN ? AND ?",
                                         (aid, start.isoformat(), end.isoformat()))]
    rej: dict[str, int] = {}
    for sg in signals:
        for x in json.loads(sg["risk_decision"] or "{}").get("rejections", []):
            k = x.split(" (")[0][:70]
            rej[k] = rej.get(k, 0) + 1
    alerts = [dict(r) for r in db.query("SELECT * FROM alerts WHERE ts>=? AND level IN ('warning','critical') ORDER BY id",
                                        (start.isoformat(),))]
    bench_ret = None
    if market is not None:
        b = market.bars(benchmark)
        b0 = b[b.index < start]
        b1 = b[b.index <= end]
        if len(b0) and len(b1):
            bench_ret = (float(b1["close"].iloc[-1]) / float(b0["close"].iloc[-1]) - 1) * 100
    eq_change = (s_end["equity_gbp"] - s_start["equity_gbp"]) if s_end and s_start else None
    return {
        "account": acc, "start": start.isoformat(), "end": end.isoformat(), "state": st, "snap_end": s_end,
        "snap_start": s_start, "fills": fills, "fees_gbp": costs, "impact_gbp": impact,
        "realised_period_gbp": realised_period, "equity_change_gbp": eq_change,
        "equity_change_pct": eq_change / s_start["equity_gbp"] * 100 if eq_change is not None and s_start["equity_gbp"] else None,
        "since_start_pct": (s_end["equity_gbp"] / acc["starting_capital"] - 1) * 100 if s_end else None,
        "signals": signals, "rejections": sorted(rej.items(), key=lambda kv: -kv[1]),
        "alerts": alerts, "benchmark": benchmark, "benchmark_return_pct": bench_ret,
    }


def render_markdown(d: dict, kind: str) -> str:
    st, se = d["state"], d["snap_end"]
    title = "Daily" if kind == "daily" else "Weekly"
    L = [f"# {title} report - SIMULATED PAPER ACCOUNT", "",
         f"Period: {d['start']} to {d['end']}  |  Account #{d['account']['id']} (started with {_money(d['account']['starting_capital'])})",
         "", "> Paper trading: no real money or real orders. Fills are simulated from end-of-day data.", "",
         "## Portfolio", ""]
    if se:
        L += [f"- Equity at close: **{_money(se['equity_gbp'])}** (cash {_money(se['cash_gbp'])}, "
              f"positions {_money(se['positions_gbp'])})",
              f"- Change over period: {_money(d['equity_change_gbp'])}"
              + (f" ({d['equity_change_pct']:+.2f}%)" if d["equity_change_pct"] is not None else ""),
              f"- Since start: {d['since_start_pct']:+.2f}%" if d["since_start_pct"] is not None else "- Since start: n/a",
              f"- Realised P&L in period: {_money(d['realised_period_gbp'])}; "
              f"unrealised P&L on open positions now: {_money(se['unrealised_pnl_gbp'])}"]
    else:
        L.append("- No valuation recorded yet (run the daily cycle).")
    if d["benchmark_return_pct"] is not None:
        L.append(f"- Benchmark {d['benchmark']} over the period: {d['benchmark_return_pct']:+.2f}% (price only)")
    L += ["", "## Open positions", ""]
    if st.positions:
        L += ["| Symbol | Qty | Avg price | Cost basis | Realised so far |", "|---|---|---|---|---|"]
        for p in st.positions.values():
            L.append(f"| {p.symbol} | {p.quantity:g} | {p.avg_price:.4f} {p.currency} | {_money(p.cost_basis_gbp)} | {_money(p.realised_pnl_gbp)} |")
    else:
        L.append("None (holding cash).")
    L += ["", "## Trades executed (simulated fills)", ""]
    if d["fills"]:
        L += ["| Date | Side | Symbol | Qty | Price | Purpose | Fees £ | Note |", "|---|---|---|---|---|---|---|---|"]
        for f in d["fills"]:
            fees = f["commission_gbp"] + f["stamp_duty_gbp"] + f["other_fees_gbp"] + f["fx_cost_gbp"]
            L.append(f"| {f['trade_date']} | {f['side']} | {f['symbol']} | {f['quantity']:g} | {f['price']:.4f} | "
                     f"{f['purpose']} | {fees:.2f} | {f['execution_note']} |")
    else:
        L.append("No fills in this period.")
    L += ["", "## Costs", "", f"- Explicit fees (commission, stamp duty, levies, FX): {_money(d['fees_gbp'])}",
          f"- Estimated spread & slippage: {_money(d['impact_gbp'])}", "",
          "## Signals and risk decisions", "",
          f"- Signals generated: {len(d['signals'])}; turned into orders: {sum(1 for s in d['signals'] if s['status'] == 'ordered')}"]
    for k, n in d["rejections"][:8]:
        L.append(f"- Rejected {n}x: {k}")
    L += ["", "## Warnings and system events", ""]
    L += [f"- {a['ts'][:16]} [{a['level']}] {a['message']}" for a in d["alerts"][-30:]] or ["- None"]
    if kind == "weekly":
        L += ["", "## What worked / what needs investigation", ""]
        wins = [f for f in d["fills"] if f["side"] == "SELL"]
        L.append(f"- Exits this week: {len(wins)}. Review stop-loss exits and any 'GAP' fills in the table above.")
        if d["benchmark_return_pct"] is not None and d["equity_change_pct"] is not None:
            diff = d["equity_change_pct"] - d["benchmark_return_pct"]
            L.append(f"- Versus benchmark this week: {diff:+.2f} percentage points (one week is far too short to judge a strategy).")
        if any(a["level"] == "critical" for a in d["alerts"]):
            L.append("- Critical events occurred - investigate before relying on results.")
    return "\n".join(L) + "\n"


def generate(db: Database, aid: int, kind: str, end: date, benchmark: str, market=None, ai_text: str | None = None) -> dict:
    start = end if kind == "daily" else end - timedelta(days=6)
    data = build_report_data(db, aid, start, end, benchmark, market)
    md = render_markdown(data, kind)
    if ai_text:
        md += ("\n## AI commentary (generated by the local language model - interpretation only, may be wrong; "
               "it does not change any numbers above)\n\n" + ai_text.strip() + "\n")
    out = paths.EXPORT_DIR / "reports"
    out.mkdir(parents=True, exist_ok=True)
    f = out / f"{kind}-{end.isoformat()}.md"
    f.write_text(md, encoding="utf-8")
    return {"path": str(f), "markdown": md, "data": data}
