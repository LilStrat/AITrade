"""Broad-market regime filter.

"Risk-on" when the benchmark (default ISF.L, FTSE 100 ETF) closes above its
200-day simple moving average. New long entries are only allowed risk-on.
This is a blunt but well-known filter: it tends to keep a long-only system
out of prolonged bear markets, at the cost of late re-entry after V-shaped
recoveries and whipsaws around the average.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from ..indicators import sma


class RegimeFilter:
    def __init__(self, benchmark_bars: pd.DataFrame, length: int = 200):
        self.length = length
        df = benchmark_bars.copy()
        if df.empty:
            self.table = pd.DataFrame(columns=["close", "sma", "risk_on"])
            return
        df["sma"] = sma(df["close"], length)
        df["risk_on"] = df["close"] > df["sma"]
        self.table = df[["close", "sma", "risk_on"]]

    def risk_on(self, day: date) -> bool:
        if day not in self.table.index:
            return False  # benchmark missing for this day -> do not open new trades
        v = self.table.loc[day, "risk_on"]
        return bool(v) if pd.notna(self.table.loc[day, "sma"]) else False

    def describe(self, day: date) -> str:
        if day not in self.table.index or pd.isna(self.table.loc[day, "sma"]):
            return "Market regime unknown (benchmark data missing) - no new entries"
        r = self.table.loc[day]
        state = "RISK-ON" if r["risk_on"] else "RISK-OFF"
        return (f"{state}: benchmark close {r['close']:.2f} vs {self.length}-day average {r['sma']:.2f} "
                f"({(r['close'] / r['sma'] - 1) * 100:+.1f}%)")
