"""Technical indicators. Every function is causal: the value on day t uses
only data from day t and earlier (tests verify this)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    """Exponential moving average, alpha = 2/(n+1); undefined for the first n-1 bars."""
    out = s.ewm(span=n, adjust=False, min_periods=n).mean()
    return out


def wilder(s: pd.Series, n: int) -> pd.Series:
    """Wilder's smoothing (alpha = 1/n), seeded with the simple mean of the first n values."""
    vals = s.to_numpy(dtype=float)
    out = np.full(len(vals), np.nan)
    idx = np.flatnonzero(~np.isnan(vals))
    if len(idx) < n:
        return pd.Series(out, index=s.index)
    first = idx[0]
    seed_end = first + n  # exclusive
    if np.isnan(vals[first:seed_end]).any():
        return pd.Series(out, index=s.index)
    tail = vals[seed_end - 1:].copy()
    tail[0] = vals[first:seed_end].mean()
    # y_t = y_{t-1} * (n-1)/n + x_t / n  ==  ewm(alpha=1/n, adjust=False); NaNs carry the last value
    sm = pd.Series(tail).ewm(alpha=1.0 / n, adjust=False, ignore_na=True).mean().to_numpy()
    out[seed_end - 1:] = sm
    return pd.Series(out, index=s.index)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder's Relative Strength Index (0..100)."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = wilder(gain.iloc[1:], n).reindex(close.index)
    avg_loss = wilder(loss.iloc[1:], n).reindex(close.index)
    rs = avg_gain / avg_loss
    out = 100 - 100 / (1 + rs)
    out = out.where(avg_loss != 0, 100.0).where(avg_gain.notna())
    out = out.where(~((avg_gain == 0) & (avg_loss == 0)), 50.0)
    return out


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    tr.iloc[0] = high.iloc[0] - low.iloc[0]
    return tr


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder's Average True Range."""
    return wilder(true_range(high, low, close), n)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    return pd.DataFrame({"mid": mid, "upper": mid + k * sd, "lower": mid - k * sd})


def rate_of_change(close: pd.Series, n: int) -> pd.Series:
    return close / close.shift(n) - 1


def highest(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).max()


def lowest(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).min()
