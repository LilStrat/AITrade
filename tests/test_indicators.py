import numpy as np
import pandas as pd
import pytest

from tradebot import indicators as ind


def test_sma_and_ema_known_values():
    s = pd.Series([1, 2, 3, 4, 5], dtype=float)
    assert list(ind.sma(s, 3).round(6))[2:] == [2.0, 3.0, 4.0]
    assert ind.sma(s, 3).isna().sum() == 2
    e = ind.ema(s, 3)  # alpha = 0.5, seeded with first value
    # ewm(adjust=False): 1, 1.5, 2.25, 3.125, 4.0625 ; first 2 hidden by min_periods
    assert e.iloc[2] == pytest.approx(2.25)
    assert e.iloc[4] == pytest.approx(4.0625)


def test_rsi_wilder_textbook_example():
    # Wilder (1978) / commonly reproduced 14-day example (closes from StockCharts' RSI article)
    closes = [44.3389, 44.0902, 44.1497, 43.6124, 44.2779, 44.8264, 45.0955, 45.4245, 45.8433, 46.0826, 45.8931,
              46.0328, 45.6140, 46.2820, 46.2820, 46.0028, 46.0328, 46.4116, 46.2222, 45.6439]
    r = ind.rsi(pd.Series(closes), 14)
    assert r.iloc[14] == pytest.approx(70.53, abs=0.05)
    assert r.iloc[15] == pytest.approx(66.32, abs=0.05)
    assert r.iloc[:14].isna().all()


def test_rsi_edges():
    up = pd.Series(np.arange(1, 30, dtype=float))
    assert ind.rsi(up, 2).dropna().eq(100).all()
    flat = pd.Series([5.0] * 10)
    assert ind.rsi(flat, 2).dropna().eq(50).all()


def test_atr_true_range_with_gap():
    high = pd.Series([10, 11, 15.0])
    low = pd.Series([9, 10, 14.0])
    close = pd.Series([9.5, 10.5, 14.5])
    tr = ind.true_range(high, low, close)
    assert list(tr) == [1.0, 1.5, 4.5]  # |11 - 9.5| = 1.5 ; gap: |15 - 10.5| = 4.5
    a = ind.atr(high, low, close, 2)
    assert a.iloc[1] == pytest.approx(1.25)              # seed: mean of first two
    assert a.iloc[2] == pytest.approx((1.25 * 1 + 4.5) / 2)


def test_bollinger_and_macd_shapes():
    s = pd.Series(np.linspace(1, 50, 60))
    bb = ind.bollinger(s, 20)
    assert (bb["upper"].dropna() >= bb["lower"].dropna()).all()
    m = ind.macd(s)
    assert (m["hist"].dropna() == (m["macd"] - m["signal"]).dropna()).all()


@pytest.mark.parametrize("fn", [
    lambda d: ind.sma(d["close"], 20), lambda d: ind.ema(d["close"], 20), lambda d: ind.rsi(d["close"], 2),
    lambda d: ind.rsi(d["close"], 14), lambda d: ind.atr(d["high"], d["low"], d["close"], 14),
    lambda d: ind.highest(d["close"], 55).shift(1), lambda d: ind.rate_of_change(d["close"], 126),
    lambda d: ind.macd(d["close"])["hist"], lambda d: ind.bollinger(d["close"])["upper"],
])
def test_indicators_are_causal(fn):
    """Lookahead check: the value at t must not change when later data changes or is removed."""
    rng = np.random.default_rng(1)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 400)))
    df = pd.DataFrame({"close": c, "high": c * 1.01, "low": c * 0.99})
    full = fn(df)
    t = 300
    truncated = fn(df.iloc[: t + 1])
    altered = df.copy()
    altered.iloc[t + 1:] *= 3.0
    alt = fn(altered)
    assert truncated.iloc[t] == pytest.approx(full.iloc[t], nan_ok=True)
    assert alt.iloc[t] == pytest.approx(full.iloc[t], nan_ok=True)
