"""Stooq free CSV daily data (fallback provider).

Limitations: no currency metadata (we rely on the currency already detected
for the instrument), no corporate-action list, and the adjustment basis is
not documented. The data store therefore only accepts Stooq updates when
they agree with already-stored prices on overlapping days.
"""

from __future__ import annotations

import io
from datetime import date

import httpx
import pandas as pd

from .base import (BAR_COLUMNS, DataProviderError, FetchResult, MarketDataProvider,
                   normalise_currency)


def to_stooq_symbol(symbol: str) -> str:
    s = symbol.lower()
    if s.endswith(".l"):
        return s[:-2] + ".uk"
    if "." not in s:
        return s + ".us"
    return s


class StooqProvider(MarketDataProvider):
    name = "stooq"
    delay_note = "End-of-day bars from Stooq (free, not real-time)"
    licence_note = "Personal use. Adjustment basis and currency are not documented by the source."
    URL = "https://stooq.com/q/d/l/"

    def __init__(self, client: httpx.Client | None = None, timeout: float = 20.0):
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=True,
                                              headers={"User-Agent": "tradebot/1.0 (personal use)"})

    def fetch_daily(self, symbol: str, start: date, end: date, currency_hint: str | None = None) -> FetchResult:
        if not currency_hint or currency_hint == "AUTO":
            raise DataProviderError(f"Stooq gives no currency; instrument {symbol} currency is unknown")
        params = {"s": to_stooq_symbol(symbol), "i": "d",
                  "d1": start.strftime("%Y%m%d"), "d2": end.strftime("%Y%m%d")}
        try:
            r = self._client.get(self.URL, params=params)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise DataProviderError(f"Stooq request failed for {symbol}: {exc}") from exc
        text = r.text.strip()
        if not text or text.lower().startswith("no data") or "Date" not in text.splitlines()[0]:
            raise DataProviderError(f"Stooq returned no usable data for {symbol}")
        raw = pd.read_csv(io.StringIO(text))
        # Stooq quotes LSE equities in pence like Yahoo; trust the stored hint.
        currency, divisor = normalise_currency(currency_hint)
        bars = pd.DataFrame({
            "date": pd.to_datetime(raw["Date"]).dt.date,
            "open": raw["Open"].astype(float) / divisor,
            "high": raw["High"].astype(float) / divisor,
            "low": raw["Low"].astype(float) / divisor,
            "close": raw["Close"].astype(float) / divisor,
            "adj_close": raw["Close"].astype(float) / divisor,
            "volume": raw.get("Volume", pd.Series(0, index=raw.index)).fillna(0).astype(float),
        }, columns=BAR_COLUMNS)
        return FetchResult(symbol, self.name, currency, bars, [], self.delay_note,
                           adjustment_note="adjustment basis not documented")
