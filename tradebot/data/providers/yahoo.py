"""Yahoo Finance daily data via the open-source ``yfinance`` library.

Limitations (shown in the dashboard):
* Unofficial. Yahoo can change or block access without notice; no SLA.
* yfinance states Yahoo data is intended for personal use only.
* LSE prices are usually quoted in pence (GBp); converted to GBP here.
* Open/High/Low/Close are split-adjusted (history is restated after a split);
  they are NOT dividend-adjusted. Dividends are returned separately.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd

from .base import (BAR_COLUMNS, CorporateAction, DataProviderError, FetchResult,
                   MarketDataProvider, normalise_currency)

log = logging.getLogger(__name__)


class YahooProvider(MarketDataProvider):
    name = "yahoo"
    delay_note = "End-of-day bars from Yahoo Finance (unofficial, not real-time)"
    licence_note = "Personal, non-commercial use only. Unofficial source with no service guarantee."

    def __init__(self, ticker_factory=None):
        # ticker_factory lets tests inject a fake instead of hitting the network.
        self._factory = ticker_factory

    def _ticker(self, symbol: str):
        if self._factory is not None:
            return self._factory(symbol)
        import yfinance as yf  # imported lazily: optional at runtime
        return yf.Ticker(symbol)

    def fetch_daily(self, symbol: str, start: date, end: date, currency_hint: str | None = None) -> FetchResult:
        t = self._ticker(symbol)
        try:
            df = t.history(start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
                           interval="1d", auto_adjust=False, actions=True, raise_errors=True)
        except Exception as exc:  # yfinance raises many types
            raise DataProviderError(f"Yahoo request failed for {symbol}: {exc}") from exc
        if df is None or df.empty:
            raise DataProviderError(f"Yahoo returned no data for {symbol}")
        meta = getattr(t, "history_metadata", None) or {}
        raw_ccy = meta.get("currency") or currency_hint
        currency, divisor = normalise_currency(raw_ccy)

        idx = pd.DatetimeIndex(df.index)
        dates = [ts.date() for ts in (idx.tz_convert("Europe/London") if idx.tz is not None else idx)]
        bars = pd.DataFrame({
            "date": dates,
            "open": df["Open"].astype(float).to_numpy() / divisor,
            "high": df["High"].astype(float).to_numpy() / divisor,
            "low": df["Low"].astype(float).to_numpy() / divisor,
            "close": df["Close"].astype(float).to_numpy() / divisor,
            "adj_close": (df["Adj Close"] if "Adj Close" in df else df["Close"]).astype(float).to_numpy() / divisor,
            "volume": df["Volume"].fillna(0).astype(float).to_numpy(),
        }, columns=BAR_COLUMNS)

        actions: list[CorporateAction] = []
        if "Dividends" in df:
            for d, v in zip(dates, df["Dividends"].fillna(0).to_numpy()):
                if v and v > 0:
                    actions.append(CorporateAction(d, "dividend", float(v) / divisor, currency))
        if "Stock Splits" in df:
            for d, v in zip(dates, df["Stock Splits"].fillna(0).to_numpy()):
                if v and v > 0 and v != 1:
                    actions.append(CorporateAction(d, "split", float(v), None))
        raw_unit = "GBX" if divisor == 100 and currency == "GBP" else currency
        return FetchResult(symbol, self.name, currency, bars, actions, self.delay_note, raw_currency=raw_unit)
