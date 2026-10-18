"""Strategy interface.

A strategy only *proposes*. It never sizes positions or places orders: the
risk manager sizes and approves, and the broker adapter executes.

Daily-bar timing contract (used identically in backtests and paper trading):
  1. After the close of day t, ``prepare`` has indicator values up to t.
  2. ``entry``/``exit`` look only at row t (and earlier).
  3. Resulting orders execute at the OPEN of the next trading day.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import date

import pandas as pd


@dataclass
class ParamSpec:
    name: str
    default: float
    min: float
    max: float
    description: str
    integer: bool = True

    def coerce(self, value) -> float:
        v = float(value)
        if not self.min <= v <= self.max:
            raise ValueError(f"{self.name} must be between {self.min} and {self.max}")
        return int(round(v)) if self.integer else v


@dataclass
class EntrySignal:
    symbol: str
    strategy: str
    as_of: date
    entry_est: float          # close on signal day (actual fill is next open)
    stop_distance: float      # price distance from entry to initial stop
    target: float | None
    rank_score: float         # ordering only - NOT a probability of profit
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)

    @property
    def stop_est(self) -> float:
        return self.entry_est - self.stop_distance

    def to_dict(self) -> dict:
        d = asdict(self)
        d["as_of"] = self.as_of.isoformat()
        d["stop_est"] = self.stop_est
        return d


@dataclass
class ExitSignal:
    symbol: str
    reason: str


@dataclass
class PositionView:
    symbol: str
    entry_date: date
    entry_price: float
    current_stop: float
    highest_close: float
    bars_held: int
    target: float | None = None


class Strategy(ABC):
    key: str = ""
    name: str = ""
    summary: str = ""
    # Plain-English documentation shown in the dashboard and docs.
    documentation: dict[str, str] = {}
    param_specs: list[ParamSpec] = []

    def __init__(self, params: dict | None = None):
        self.params = {p.name: p.default for p in self.param_specs}
        for k, v in (params or {}).items():
            spec = next((p for p in self.param_specs if p.name == k), None)
            if spec is not None:
                self.params[k] = spec.coerce(v)

    @property
    def warmup_bars(self) -> int:
        return 0

    @abstractmethod
    def prepare(self, bars: pd.DataFrame) -> pd.DataFrame:
        """Return ``bars`` with indicator columns added (causal only)."""

    @abstractmethod
    def entry(self, symbol: str, ind: pd.DataFrame, day: date, regime_ok: bool) -> EntrySignal | None:
        ...

    @abstractmethod
    def exit(self, ind: pd.DataFrame, day: date, pos: PositionView) -> ExitSignal | None:
        ...

    def trail_stop(self, ind: pd.DataFrame, day: date, pos: PositionView) -> float | None:
        """Return a new (higher) stop level, or None to keep the current one."""
        return None

    def describe(self) -> dict:
        return {
            "key": self.key, "name": self.name, "summary": self.summary,
            "documentation": self.documentation,
            "params": [{**asdict(p), "value": self.params[p.name]} for p in self.param_specs],
        }


class IndicatorTable:
    """Prepared indicators with fast row lookup by date (dict of dicts)."""

    def __init__(self, df: pd.DataFrame, from_day: date | None = None):
        """``from_day`` limits the fast lookup to the days that will be queried
        (indicator values themselves are always computed on full history)."""
        self.df = df
        part = df if from_day is None else df[df.index >= from_day]
        self.rows: dict = part.to_dict("index")

    @property
    def index(self):
        return self.df.index


def row_at(ind, day: date) -> dict | None:
    if isinstance(ind, IndicatorTable):
        return ind.rows.get(day)
    try:
        return ind.loc[day].to_dict()
    except KeyError:
        return None


def any_nan(row: dict, keys) -> bool:
    for k in keys:
        v = row.get(k)
        if v is None or v != v:  # NaN != NaN
            return True
    return False
