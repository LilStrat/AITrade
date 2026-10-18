"""EODHD (eodhd.com) end-of-day data - optional paid/limited-free provider.

Requires EODHD_API_TOKEN in the .env file. The free tier allows ~20 calls
per day (enough to test a handful of symbols); paid plans start at about
$19.99/month (check eodhd.com/pricing). EODHD's raw OHLC are unadjusted,
so this adapter applies split adjustment itself using the splits endpoint,
giving the same basis as the Yahoo provider.
"""

from __future__ import annotations

from datetime import date

import httpx
import pandas as pd

from ...config import get_secret
from .base import (BAR_COLUMNS, CorporateAction, DataProviderError, FetchResult,
                   MarketDataProvider, normalise_currency)


def to_eodhd_symbol(symbol: str) -> str:
    s = symbol.upper()
    if s.endswith(".L"):
        return s[:-2] + ".LSE"
    if "." not in s:
        return s + ".US"
    return s


class EodhdProvider(MarketDataProvider):
    name = "eodhd"
    delay_note = "End-of-day bars from EODHD (paid/limited free, not real-time)"
    licence_note = "Licensed for personal use under your EODHD plan. Do not redistribute."
    BASE = "https://eodhd.com/api"

    def __init__(self, client: httpx.Client | None = None, token: str | None = None):
        self._token = token or get_secret("EODHD_API_TOKEN")
        self._client = client or httpx.Client(timeout=30.0)

    def _get(self, path: str, params: dict) -> list | dict:
        if not self._token:
            raise DataProviderError("EODHD_API_TOKEN is not set in .env")
        try:
            r = self._client.get(f"{self.BASE}/{path}", params={**params, "api_token": self._token, "fmt": "json"})
            if r.status_code == 429:
                raise DataProviderError("EODHD rate limit reached (429)")
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as exc:
            # never include the URL (it contains the token)
            raise DataProviderError(f"EODHD request failed: {type(exc).__name__}") from None

    def fetch_daily(self, symbol: str, start: date, end: date, currency_hint: str | None = None) -> FetchResult:
        code = to_eodhd_symbol(symbol)
        if not currency_hint or currency_hint == "AUTO":
            currency_hint = "GBX" if code.endswith(".LSE") else "USD"
        currency, divisor = normalise_currency(currency_hint)
        rows = self._get(f"eod/{code}", {"from": start.isoformat(), "to": end.isoformat()})
        if not isinstance(rows, list) or not rows:
            raise DataProviderError(f"EODHD returned no data for {symbol}")
        raw = pd.DataFrame(rows)
        raw["date"] = pd.to_datetime(raw["date"]).dt.date
        splits = self._get(f"splits/{code}", {"from": "1990-01-01"}) or []
        divs = self._get(f"div/{code}", {"from": start.isoformat()}) or []

        factor = pd.Series(1.0, index=raw.index)
        actions: list[CorporateAction] = []
        for s in splits if isinstance(splits, list) else []:
            try:
                d = pd.to_datetime(s["date"]).date()
                new, old = (float(x) for x in str(s["split"]).split("/"))
                ratio = new / old
            except (KeyError, ValueError, ZeroDivisionError):
                continue
            if ratio > 0 and ratio != 1:
                factor[raw["date"] < d] *= ratio
                if start <= d <= end:
                    actions.append(CorporateAction(d, "split", ratio))
        for dv in divs if isinstance(divs, list) else []:
            try:
                d = pd.to_datetime(dv["date"]).date()
                val = float(dv.get("value") or 0)
            except (KeyError, ValueError):
                continue
            if val > 0:
                actions.append(CorporateAction(d, "dividend", val / divisor, currency))
        bars = pd.DataFrame({
            "date": raw["date"],
            "open": raw["open"].astype(float) / factor / divisor,
            "high": raw["high"].astype(float) / factor / divisor,
            "low": raw["low"].astype(float) / factor / divisor,
            "close": raw["close"].astype(float) / factor / divisor,
            "adj_close": raw.get("adjusted_close", raw["close"]).astype(float) / divisor,
            "volume": raw["volume"].fillna(0).astype(float) * factor,
        }, columns=BAR_COLUMNS)
        return FetchResult(symbol, self.name, currency, bars, actions, self.delay_note, raw_currency=currency_hint)
