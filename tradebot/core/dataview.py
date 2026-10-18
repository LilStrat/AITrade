"""Read-only market data views used by the trading core.

``FrameDataView`` serves preloaded data frames (backtests, fast).
``StoreDataView`` wraps the live ``MarketData`` store (paper trading).
Both expose identical methods so the same trading logic runs on either.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from ..data import calendar as cal


class FrameDataView:
    def __init__(self, frames: dict[str, pd.DataFrame], instruments: dict[str, dict],
                 currencies: dict[str, str], actions: dict[str, list[dict]] | None = None,
                 fx_frames: dict[str, pd.DataFrame] | None = None, stale_after_sessions: int = 1):
        self.frames = frames
        self.instruments = instruments
        self.currencies = currencies
        self._actions = actions or {}
        self.fx_frames = fx_frames or {}
        self.stale_after = stale_after_sessions

    def symbols(self) -> list[str]:
        return list(self.frames)

    def history(self, symbol: str) -> pd.DataFrame:
        return self.frames.get(symbol, pd.DataFrame(columns=["open", "high", "low", "close", "volume"]))

    def bar(self, symbol: str, day: date) -> dict | None:
        df = self.frames.get(symbol)
        if df is None or day not in df.index:
            return None
        r = df.loc[day]
        return {"open": float(r["open"]), "high": float(r["high"]), "low": float(r["low"]),
                "close": float(r["close"]), "volume": float(r["volume"])}

    def last_close(self, symbol: str, day: date) -> tuple[float, date] | None:
        df = self.frames.get(symbol)
        if df is None or df.empty:
            return None
        sub = df[df.index <= day]
        if sub.empty:
            return None
        return float(sub["close"].iloc[-1]), sub.index[-1]

    def instrument(self, symbol: str) -> dict:
        return self.instruments.get(symbol, {"symbol": symbol, "type": "stock", "sector": "Unknown",
                                             "tier": "default", "exchange": "LSE"})

    def currency(self, symbol: str) -> str:
        return self.currencies.get(symbol, "GBP")

    def fx_to_gbp(self, currency: str, day: date) -> float | None:
        if currency == "GBP":
            return 1.0
        from ..data.store import FX_SYMBOLS
        df = self.fx_frames.get(FX_SYMBOLS.get(currency, ""))
        if df is None or df.empty:
            return None
        sub = df[df.index <= day]
        if sub.empty or (day - sub.index[-1]).days > 7:
            return None
        return 1.0 / float(sub["close"].iloc[-1])

    def actions_on(self, symbol: str, day: date) -> list[dict]:
        return [a for a in self._actions.get(symbol, []) if a["date"] == day]

    def freshness(self, symbol: str, day: date) -> tuple[bool, str]:
        """In a backtest, data is 'fresh' if the symbol has a bar for ``day``."""
        lc = self.last_close(symbol, day)
        if lc is None:
            return False, f"No data for {symbol}"
        _, last = lc
        behind = len(cal.trading_days_between(last, day)) - 1 if symbol.endswith(".L") else (day - last).days
        if behind > self.stale_after:
            return False, f"{symbol} data is {behind} session(s) old"
        return True, f"{symbol} data up to {last}"


class StoreDataView(FrameDataView):
    """Live store wrapper: loads frames from the database once per cycle."""

    def __init__(self, market, symbols: list[str], stale_after_sessions: int = 1, now=None):
        frames, instruments, currencies, actions = {}, {}, {}, {}
        for s in symbols:
            df = market.bars(s)
            frames[s] = df
            instruments[s] = market.instrument(s) or {"symbol": s}
            currencies[s] = market.price_currency(s)
            actions[s] = market.actions(s)
        from ..data.store import FX_SYMBOLS
        fx = {v: market.bars(v) for v in FX_SYMBOLS.values()}
        super().__init__(frames, instruments, currencies, actions, fx, stale_after_sessions)
        self.market = market
        self.now = now

    def freshness(self, symbol: str, day: date) -> tuple[bool, str]:
        st = self.market.status(symbol, self.now)
        if st.get("state") == "fresh":
            return True, f"{symbol} data fresh (last bar {st.get('last_bar_date')}, source {st.get('source')})"
        return False, f"{symbol} data not usable: {st.get('state')} (last bar {st.get('last_bar_date')})"
