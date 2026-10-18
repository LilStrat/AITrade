"""Load stored market data into a fast in-memory view for backtests."""

from __future__ import annotations

from datetime import date

from ..core.dataview import FrameDataView
from ..data.providers.synthetic import SyntheticProvider
from ..data.store import FX_SYMBOLS, MarketData


def load_view(market: MarketData, symbols: list[str], benchmark: str) -> FrameDataView:
    syms = list(dict.fromkeys(symbols + [benchmark]))
    frames, ins, ccy, acts = {}, {}, {}, {}
    sources = set()
    for s in syms:
        df = market.bars(s)
        if df.empty:
            continue
        sources |= set(df["source"].dropna().unique())
        frames[s] = df
        ins[s] = market.instrument(s) or {"symbol": s, "type": "stock"}
        ccy[s] = market.price_currency(s)
        acts[s] = market.actions(s)
    fx = {v: market.bars(v) for v in FX_SYMBOLS.values()}
    view = FrameDataView(frames, ins, ccy, acts, fx, int(market.settings["data"]["stale_after_sessions"]))
    view.source_note = "Stored data from: " + (", ".join(sorted(sources)) or "none")
    return view


def synthetic_view(symbols: list[str], universe: list[dict], start: date, end: date) -> FrameDataView:
    """SYNTHETIC data for demonstrations when no real data has been downloaded."""
    prov = SyntheticProvider()
    by = {u["symbol"]: u for u in universe}
    frames, ins, ccy, acts = {}, {}, {}, {}
    for s in symbols:
        r = prov.fetch_daily(s, start, end)
        frames[s] = r.bars.set_index("date")
        ins[s] = by.get(s, {"symbol": s, "type": "stock", "sector": "Unknown", "tier": "default", "exchange": "LSE"})
        ccy[s] = "GBP"
        acts[s] = [{"date": a.date, "type": a.type, "value": a.value, "currency": a.currency} for a in r.actions]
    view = FrameDataView(frames, ins, ccy, acts)
    view.source_note = "SYNTHETIC random data - results are meaningless as evidence"
    return view
