"""Synthetic (made-up) prices for tests and demonstrations.

Deterministic per symbol. Clearly labelled "SYNTHETIC" everywhere it is
used. Never use synthetic results as evidence that a strategy works.
"""

from __future__ import annotations

import hashlib
from datetime import date

import numpy as np
import pandas as pd

from ..calendar import trading_days_between
from .base import BAR_COLUMNS, CorporateAction, DataProviderError, FetchResult, MarketDataProvider


def _seed(symbol: str, salt: int) -> int:
    return int(hashlib.sha256(f"{symbol}:{salt}".encode()).hexdigest()[:8], 16)


def make_bars(symbol: str, start: date, end: date, salt: int = 0, start_price: float | None = None,
              drift: float = 0.06, vol: float = 0.22) -> tuple[pd.DataFrame, list[CorporateAction]]:
    days = trading_days_between(start, end)
    if not days:
        return pd.DataFrame(columns=BAR_COLUMNS), []
    rng = np.random.default_rng(_seed(symbol, salt))
    n = len(days)
    # Regime-switching drift so trends and corrections both occur.
    regime = np.cumsum(rng.normal(0, 1, n)) / np.sqrt(np.arange(1, n + 1))
    mu = (drift + 0.25 * np.tanh(regime)) / 252
    sigma = vol / np.sqrt(252)
    rets = rng.normal(mu, sigma, n)
    p0 = start_price or float(rng.uniform(2, 60))
    close = p0 * np.exp(np.cumsum(rets))
    gap = rng.normal(0, sigma * 0.4, n)
    open_ = np.concatenate([[p0], close[:-1]]) * np.exp(gap)
    hi = np.maximum(open_, close) * np.exp(np.abs(rng.normal(0, sigma * 0.5, n)))
    lo = np.minimum(open_, close) * np.exp(-np.abs(rng.normal(0, sigma * 0.5, n)))
    volume = rng.lognormal(mean=14, sigma=0.4, size=n).round()
    bars = pd.DataFrame({"date": days, "open": open_, "high": hi, "low": lo, "close": close,
                         "adj_close": close, "volume": volume}, columns=BAR_COLUMNS)
    actions = []
    for i in range(60, n, 126):  # semi-annual dividend ~1.5% yield
        actions.append(CorporateAction(days[i], "dividend", round(float(close[i - 1]) * 0.0075, 4), "GBP"))
    return bars, actions


_CACHE: dict[tuple[str, int], tuple[pd.DataFrame, list[CorporateAction]]] = {}
ORIGIN, HORIZON = date(2005, 1, 3), date(2035, 12, 31)


def _cached(symbol: str, salt: int):
    key = (symbol, salt)
    if key not in _CACHE:
        _CACHE[key] = make_bars(symbol, ORIGIN, HORIZON, salt)
    return _CACHE[key]


class SyntheticProvider(MarketDataProvider):
    name = "synthetic"
    delay_note = "SYNTHETIC test data - not real prices"
    licence_note = "Generated locally for testing."

    def __init__(self, salt: int = 0, fail_symbols: set[str] | None = None):
        self.salt = salt
        self.fail_symbols = fail_symbols or set()

    def fetch_daily(self, symbol: str, start: date, end: date, currency_hint: str | None = None) -> FetchResult:
        if symbol in self.fail_symbols:
            raise DataProviderError(f"synthetic failure for {symbol}")
        # Generate a fixed horizon, then slice, so overlapping requests agree.
        bars, actions = _cached(symbol, self.salt)
        bars = bars[(bars["date"] >= start) & (bars["date"] <= end)].reset_index(drop=True)
        actions = [a for a in actions if start <= a.date <= end]
        if bars.empty:
            raise DataProviderError(f"no synthetic data for {symbol} in range")
        ccy = "USD" if currency_hint == "USD" else "GBP"
        return FetchResult(symbol, self.name, ccy, bars, actions, self.delay_note)
