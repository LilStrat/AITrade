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
        warnings: list[str] = []
        if "Dividends" in df:
            closes = df["Close"].astype(float).to_numpy()
            for i, (d, v) in enumerate(zip(dates, df["Dividends"].fillna(0).to_numpy())):
                if not v or v <= 0:
                    continue
                ref = closes[i - 1] if i > 0 else closes[i]
                value, note = dividend_in_major_units(float(v), float(ref), divisor)
                if note:
                    warnings.append(f"{symbol} dividend on {d}: {note}")
                if value is not None:
                    actions.append(CorporateAction(d, "dividend", value, currency))
        if "Stock Splits" in df:
            for d, v in zip(dates, df["Stock Splits"].fillna(0).to_numpy()):
                if v and v > 0 and v != 1:
                    actions.append(CorporateAction(d, "split", float(v), None))
        raw_unit = "GBX" if divisor == 100 and currency == "GBP" else currency
        return FetchResult(symbol, self.name, currency, bars, actions, self.delay_note, raw_currency=raw_unit,
                           warnings=warnings)


def dividend_in_major_units(value: float, ref_price: float, divisor: float) -> tuple[float | None, str | None]:
    """Convert a dividend to major units, guarding against unit mix-ups.

    On LSE, Yahoo sometimes reports dividends in pounds while prices are in
    pence. A per-payment yield below 0.05% in pence terms but plausible in
    pounds is treated as already being in pounds. Implausible values (above
    25% of the share price) are discarded.
    """
    if ref_price <= 0:
        return None, "no reference price; dividend ignored"
    note = None
    if divisor != 1 and value / ref_price < 0.0005 and value * divisor / ref_price <= 0.25:
        major = value
        note = "reported in pounds while prices are in pence; used as pounds"
    else:
        major = value / divisor
    if major / (ref_price / divisor) > 0.25:
        return None, f"implausible dividend ({major:.4f} vs price {ref_price / divisor:.4f}); ignored"
    return major, note
