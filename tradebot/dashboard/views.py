"""Data assembly for dashboard pages. Everything shown comes from the
database / live components - no hard-coded example figures."""

from __future__ import annotations

import json
from datetime import date, timedelta

from ..data import calendar as cal
from ..portfolio.ledger import Ledger
from ..portfolio.tax import compute_disposals, summary_by_tax_year
from ..strategies import REGISTRY

MODE_LABELS = {
    "local_paper": ("PAPER TRADING - SIMULATED MONEY", "paper"),
    "broker_paper": ("BROKER PAPER - NOT AVAILABLE IN THIS VERSION", "warn"),
    "live": ("LIVE MODE REQUESTED - DISABLED, NO ORDERS WILL BE SENT", "danger"),
}


def mode_banner(settings: dict) -> dict:
    text, cls = MODE_LABELS.get(settings["app"]["mode"], ("UNKNOWN MODE", "danger"))
    return {"text": text, "cls": cls}


def account_summary(ctx) -> dict:
    acc = ctx.service.account()
    aid = acc["id"]
    ledger = Ledger(ctx.db)
    try:
        st = ledger.state(aid)
        error = None
    except ValueError as exc:
        return {"account": acc, "error": str(exc)}
    snaps = [dict(r) for r in ctx.db.query("SELECT * FROM equity_snapshots WHERE account_id=? ORDER BY date", (aid,))]
    last = snaps[-1] if snaps else None
    prev = snaps[-2] if len(snaps) > 1 else None
    positions = []
    total_val = 0.0
    for sym, p in st.positions.items():
        bars = ctx.market.bars(sym)
        price = float(bars["close"].iloc[-1]) if not bars.empty else None
        ccy = ctx.market.price_currency(sym)
        fx = ctx.market.fx_to_gbp(ccy, date.today()) if ccy != "GBP" else 1.0
        val = p.quantity * price * fx if price is not None and fx else None
        meta = ctx.db.one("SELECT * FROM position_meta WHERE account_id=? AND symbol=?", (aid, sym))
        ins = ctx.market.instrument(sym) or {}
        positions.append({
            "symbol": sym, "name": ins.get("name", sym), "sector": ins.get("sector", ""), "qty": p.quantity,
            "avg_price": p.avg_price, "currency": p.currency, "price": price,
            "price_date": str(bars.index[-1]) if not bars.empty else None, "value_gbp": val,
            "cost_gbp": p.cost_basis_gbp, "unrealised_gbp": (val - p.cost_basis_gbp) if val is not None else None,
            "stop": meta["current_stop"] if meta else None, "strategy": meta["strategy"] if meta else None,
            "entry_date": meta["entry_date"] if meta else None, "bars_held": meta["bars_held"] if meta else None,
            "dividends_gbp": p.dividends_gbp})
        total_val += val or 0.0
    equity = st.cash_gbp + total_val
    for p in positions:
        p["weight_pct"] = (p["value_gbp"] / equity * 100) if p["value_gbp"] and equity else None
    return {
        "account": acc, "state": st, "positions": positions, "positions_value": total_val, "equity": equity,
        "cash": st.cash_gbp, "reserved": st.reserved_gbp, "available": st.available_cash_gbp,
        "realised": st.realised_pnl_gbp, "dividends": st.dividends_gbp, "fees": st.fees_gbp,
        "unrealised": sum(p["unrealised_gbp"] or 0 for p in positions),
        "since_start_pct": (equity / acc["starting_capital"] - 1) * 100 if acc["starting_capital"] else None,
        "day_change": (last["equity_gbp"] - prev["equity_gbp"]) if last and prev else None,
        "last_snapshot": last, "snaps": snaps, "error": error,
        "exposure_pct": total_val / equity * 100 if equity else 0,
    }


def data_health(ctx) -> dict:
    rows = []
    for ins in ctx.market.instruments():
        st = ctx.market.status(ins["symbol"])
        rows.append({**ins, **st})
    fresh = sum(1 for r in rows if r.get("state") == "fresh")
    prov = ctx.market.primary.describe()
    last_ok = ctx.db.one("SELECT MAX(last_success_at) FROM data_status")[0]
    return {"rows": rows, "fresh": fresh, "total": len(rows), "provider": prov, "last_success": last_ok,
            "fallback": ctx.market.fallback.describe() if ctx.market.fallback else None,
            "expected": str(cal.last_completed_session()),
            "events": [dict(r) for r in ctx.db.query("SELECT * FROM data_quality_events ORDER BY id DESC LIMIT 40")]}


def watchlist_table(ctx) -> list[dict]:
    out = []
    for ins in ctx.market.instruments():
        b = ctx.market.bars(ins["symbol"])
        if len(b) < 2:
            out.append({**ins, "close": None})
            continue
        c = b["close"]
        def ret(n):
            return (float(c.iloc[-1]) / float(c.iloc[-1 - n]) - 1) * 100 if len(c) > n else None
        sma200 = float(c.tail(200).mean()) if len(c) >= 200 else None
        out.append({**ins, "close": float(c.iloc[-1]), "date": str(b.index[-1]), "r1": ret(1), "r5": ret(5),
                    "r21": ret(21), "r63": ret(63), "above200": (float(c.iloc[-1]) > sma200) if sma200 else None,
                    "ccy": ctx.market.price_currency(ins["symbol"])})
    return out


def market_facts(ctx) -> dict:
    """Deterministic market summary (also the input to the AI summary)."""
    wl = [w for w in watchlist_table(ctx) if w.get("close") is not None]
    bench = ctx.settings["universe"]["benchmark"]
    from ..strategies.regime import RegimeFilter
    bb = ctx.market.bars(bench)
    regime = RegimeFilter(bb).describe(bb.index[-1]) if not bb.empty else "benchmark data missing"
    above = [w for w in wl if w.get("above200") is not None]
    return {
        "date": str(bb.index[-1]) if not bb.empty else None,
        "benchmark": bench, "regime": regime,
        "instruments_with_data": len(wl),
        "pct_above_200d_average": round(sum(1 for w in above if w["above200"]) / len(above) * 100, 1) if above else None,
        "best_1m": sorted([(w["symbol"], round(w["r21"], 2)) for w in wl if w.get("r21") is not None], key=lambda x: -x[1])[:5],
        "worst_1m": sorted([(w["symbol"], round(w["r21"], 2)) for w in wl if w.get("r21") is not None], key=lambda x: x[1])[:5],
        "data_warnings": [r["symbol"] for r in data_health(ctx)["rows"] if r.get("state") != "fresh"][:15],
        "data_note": ctx.market.primary.delay_note,
    }


def signals(ctx, days: int = 10) -> list[dict]:
    since = (date.today() - timedelta(days=days)).isoformat()
    acc = ctx.service.account()
    out = []
    for r in ctx.db.query("SELECT * FROM signals WHERE account_id=? AND as_of_date>=? ORDER BY as_of_date DESC, id DESC",
                          (acc["id"], since)):
        d = dict(r)
        d["reasons_l"] = json.loads(d["reasons"] or "[]")
        d["risks_l"] = json.loads(d["risks"] or "[]")
        d["risk"] = json.loads(d["risk_decision"] or "{}")
        d["strategy_name"] = REGISTRY[d["strategy"]].name if d["strategy"] in REGISTRY else d["strategy"]
        out.append(d)
    return out


def orders(ctx, limit: int = 300) -> list[dict]:
    acc = ctx.service.account()
    return [dict(r) for r in ctx.db.query("SELECT * FROM orders WHERE account_id=? ORDER BY created_at DESC LIMIT ?",
                                          (acc["id"], limit))]


def fills(ctx, limit: int = 300) -> list[dict]:
    acc = ctx.service.account()
    return [dict(r) for r in ctx.db.query("""SELECT f.*, o.purpose, o.strategy, o.reason FROM fills f JOIN orders o
                                             ON o.id=f.order_id WHERE f.account_id=? ORDER BY f.trade_date DESC, f.ts DESC LIMIT ?""",
                                          (acc["id"], limit))]


def tax_summary(ctx) -> dict:
    acc = ctx.service.account()
    txs = Ledger(ctx.db).transactions(acc["id"])
    disposals = compute_disposals(txs)
    return {"disposals": disposals,
            "years": summary_by_tax_year(disposals, [t for t in txs if t["type"] == "DIVIDEND"],
                                         ctx.settings["account"]["type"])}


def risk_view(ctx, summary: dict) -> dict:
    acc = summary["account"]
    rs = dict(ctx.db.one("SELECT * FROM risk_state WHERE account_id=?", (acc["id"],)) or {})
    eq = summary.get("equity") or 0
    peak = rs.get("peak_equity") or eq
    dd = (peak - eq) / peak * 100 if peak else 0
    day_start = rs.get("day_start_equity")
    last = summary.get("last_snapshot")
    daily = ((day_start - last["equity_gbp"]) / day_start * 100) if day_start and last else 0
    sectors: dict[str, float] = {}
    for p in summary.get("positions", []):
        sectors[p["sector"]] = sectors.get(p["sector"], 0) + (p["weight_pct"] or 0)
    r = ctx.settings["risk"]
    return {"state": rs, "drawdown_pct": dd, "daily_loss_pct": max(daily, 0), "sectors": sectors,
            "limits": r, "open_positions": len(summary.get("positions", [])),
            "exposure_pct": summary.get("exposure_pct", 0),
            "near": [msg for cond, msg in (
                (dd >= r["max_drawdown_pct"] * r["warn_at_pct_of_limit"] / 100, f"Drawdown {dd:.2f}% approaching {r['max_drawdown_pct']}% limit"),
                (len(summary.get("positions", [])) >= r["max_open_positions"], "Maximum number of positions reached"),
                (summary.get("exposure_pct", 0) >= r["max_total_exposure_pct"] * r["warn_at_pct_of_limit"] / 100, "Exposure approaching its limit"),
            ) if cond]}
