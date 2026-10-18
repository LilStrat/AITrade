"""Data quality checks for downloaded bars (downloaded data is untrusted)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from .calendar import is_trading_day


@dataclass
class ValidationReport:
    clean: pd.DataFrame
    issues: list[tuple[str, str]] = field(default_factory=list)  # (severity, message)
    suspicious: bool = False

    def add(self, severity: str, msg: str) -> None:
        self.issues.append((severity, msg))


def validate_bars(bars: pd.DataFrame, *, suspicious_move_pct: float = 35.0,
                  last_complete: date | None = None, exchange: str = "LSE") -> ValidationReport:
    """Clean and check daily bars. Never silently invents prices.

    * drops rows with missing/non-positive prices or impossible OHLC
    * drops duplicate dates (keeps the last one received)
    * drops bars dated after the last completed session (incomplete candle)
    * repairs isolated 100x pence/pound unit errors, and records the repair
    * flags (does not delete) other unusually large one-day moves
    """
    rep = ValidationReport(clean=bars.copy())
    df = rep.clean
    if df.empty:
        rep.add("error", "no bars")
        return rep

    df["date"] = pd.to_datetime(df["date"]).dt.date
    num = ["open", "high", "low", "close", "adj_close", "volume"]
    for c in num:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    dupes = int(df.duplicated("date", keep="last").sum())
    if dupes:
        rep.add("warning", f"{dupes} duplicate date(s) removed")
        df = df.drop_duplicates("date", keep="last")

    bad = df[["open", "high", "low", "close"]].isna().any(axis=1) | (df[["open", "high", "low", "close"]] <= 0).any(axis=1)
    if bad.any():
        rep.add("warning", f"{int(bad.sum())} bar(s) with missing or non-positive prices removed")
        df = df[~bad]

    df["volume"] = df["volume"].fillna(0).clip(lower=0)
    df["adj_close"] = df["adj_close"].fillna(df["close"])

    # Fix small inconsistencies (high below open/close etc.) conservatively.
    hi = df[["open", "high", "close"]].max(axis=1)
    lo = df[["open", "low", "close"]].min(axis=1)
    inconsistent = (df["high"] < hi - 1e-12) | (df["low"] > lo + 1e-12)
    if inconsistent.any():
        rep.add("warning", f"{int(inconsistent.sum())} bar(s) had high/low inconsistent with open/close; widened")
        df["high"], df["low"] = hi.where(inconsistent, df["high"]), lo.where(inconsistent, df["low"])

    if last_complete is not None:
        future = df["date"] > last_complete
        if future.any():
            rep.add("info", f"{int(future.sum())} incomplete/future bar(s) ignored")
            df = df[~future]

    if exchange == "LSE":
        non_session = ~df["date"].map(is_trading_day)
        if non_session.any():
            rep.add("warning", f"{int(non_session.sum())} bar(s) on non-trading days removed")
            df = df[~non_session]

    df = df.sort_values("date").reset_index(drop=True)
    df = _repair_unit_errors(df, rep)

    if len(df) > 1:
        ret = df["close"].pct_change().abs() * 100
        big = ret > suspicious_move_pct
        if big.any():
            days = ", ".join(str(d) for d in df.loc[big, "date"].tail(5))
            rep.add("warning", f"{int(big.sum())} one-day move(s) above {suspicious_move_pct}% (e.g. {days})")
            # Only the most recent bars matter for trading safety.
            if big.tail(5).any():
                rep.suspicious = True
    rep.clean = df
    return rep


def _repair_unit_errors(df: pd.DataFrame, rep: ValidationReport) -> pd.DataFrame:
    """Yahoo sometimes reports single LSE bars in pounds instead of pence (or
    vice versa), producing an isolated ~100x jump that reverts next day."""
    if len(df) < 3:
        return df
    c = df["close"].to_numpy()
    prev, nxt = np.roll(c, 1), np.roll(c, -1)
    ratio_prev, ratio_next = c / prev, c / nxt
    for factor in (100.0, 0.01):
        lo, hi = factor * 0.7, factor * 1.3
        mask = (ratio_prev > lo) & (ratio_prev < hi) & (ratio_next > lo) & (ratio_next < hi)
        mask[0] = mask[-1] = False
        if mask.any():
            cols = ["open", "high", "low", "close", "adj_close"]
            df.loc[mask, cols] = df.loc[mask, cols] / factor
            days = ", ".join(str(d) for d in df.loc[mask, "date"])
            rep.add("warning", f"repaired {int(mask.sum())} bar(s) with a 100x unit error: {days}")
    return df


def check_overlap(stored: pd.DataFrame, fresh: pd.DataFrame, tolerance_pct: float = 0.5) -> tuple[bool, str]:
    """Do newly fetched bars agree with stored bars on overlapping dates?

    A disagreement usually means history was restated (e.g. after a stock
    split) or the sources use different adjustment bases.
    """
    if stored.empty or fresh.empty:
        return True, "no overlap to compare"
    m = stored[["date", "close"]].merge(fresh[["date", "close"]], on="date", suffixes=("_s", "_f"))
    if m.empty:
        return True, "no overlapping dates"
    m = m.tail(10)
    diff = ((m["close_f"] - m["close_s"]).abs() / m["close_s"] * 100).max()
    if diff > tolerance_pct:
        return False, f"overlapping closes differ by up to {diff:.2f}%"
    return True, f"overlap agrees (max diff {diff:.3f}%)"
