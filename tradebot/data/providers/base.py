"""Market data provider interface.

Every provider returns daily bars in a common shape. Prices are converted to
the MAJOR currency unit (GBX/GBp pence -> GBP pounds) so the rest of the
application never has to think about pence.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

BAR_COLUMNS = ["date", "open", "high", "low", "close", "adj_close", "volume"]


class DataProviderError(RuntimeError):
    """Raised when a provider cannot supply the requested data."""


@dataclass
class CorporateAction:
    date: date
    type: str          # 'dividend' | 'split'
    value: float       # dividend per share (major units) or split ratio (new/old)
    currency: str | None = None


@dataclass
class FetchResult:
    symbol: str
    source: str
    currency: str                       # major unit, e.g. GBP or USD
    bars: pd.DataFrame                  # columns BAR_COLUMNS, 'date' is datetime.date
    actions: list[CorporateAction] = field(default_factory=list)
    delay_note: str = "End-of-day data"
    adjustment_note: str = "split-adjusted, not dividend-adjusted"
    raw_currency: str | None = None     # provider's own unit, e.g. GBX (pence)
    warnings: list[str] = field(default_factory=list)  # recorded as data-quality events


def normalise_currency(raw: str | None) -> tuple[str, float]:
    """Map a provider currency code to (major currency, price divisor)."""
    if not raw:
        raise DataProviderError("currency unknown")
    if raw in ("GBp", "GBX", "GBx"):
        return "GBP", 100.0
    if raw.upper() == "ZAC":  # South African cents
        return "ZAR", 100.0
    if raw == "ILA":
        return "ILS", 100.0
    return raw.upper(), 1.0


def empty_bars() -> pd.DataFrame:
    return pd.DataFrame(columns=BAR_COLUMNS)


class MarketDataProvider(ABC):
    name: str = "base"
    is_real_time: bool = False
    delay_note: str = "End-of-day data"
    licence_note: str = ""

    @abstractmethod
    def fetch_daily(self, symbol: str, start: date, end: date, currency_hint: str | None = None) -> FetchResult:
        """Return daily bars for ``symbol`` with dates in [start, end]."""

    def describe(self) -> dict:
        return {
            "name": self.name,
            "real_time": self.is_real_time,
            "delay_note": self.delay_note,
            "licence_note": self.licence_note,
        }
