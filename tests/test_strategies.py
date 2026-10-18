import numpy as np

from tradebot.strategies.base import IndicatorTable, PositionView
from tradebot.strategies.breakout_momentum import BreakoutMomentum
from tradebot.strategies.regime import RegimeFilter
from tradebot.strategies.trend_pullback import TrendPullback
from conftest import make_frame


def uptrend_with_dip():
    closes = list(np.linspace(50, 100, 260))
    closes += [99.0, 97.5, 96.5]                # two-three down days: RSI(2) collapses
    return make_frame(closes)


def test_trend_pullback_entry_rules():
    st = TrendPullback()
    df = uptrend_with_dip()
    ind = IndicatorTable(st.prepare(df))
    day = df.index[-1]
    sig = st.entry("AAA.L", ind, day, regime_ok=True)
    assert sig is not None
    assert sig.entry_est == 96.5 and sig.stop_distance > 0
    assert any("RSI(2)" in r for r in sig.reasons) and sig.risks
    assert st.entry("AAA.L", ind, day, regime_ok=False) is None   # regime filter
    assert st.entry("AAA.L", ind, df.index[-4], regime_ok=True) is None  # no dip yet


def test_trend_pullback_rejects_downtrend_and_crash():
    st = TrendPullback()
    down = make_frame(list(np.linspace(100, 50, 260)) + [49, 48, 47])
    assert st.entry("X", IndicatorTable(st.prepare(down)), down.index[-1], True) is None
    crash = make_frame(list(np.linspace(50, 100, 260)) + [95, 88, 80])  # -20% in days
    assert st.entry("X", IndicatorTable(st.prepare(crash)), crash.index[-1], True) is None


def test_trend_pullback_exits():
    st = TrendPullback()
    df = make_frame(list(np.linspace(50, 100, 260)) + [97, 96, 99, 101])
    ind = IndicatorTable(st.prepare(df))
    pos = PositionView("X", df.index[-3], 96, 90, 96, 2)
    assert "bounce" in st.exit(ind, df.index[-1], pos).reason
    pos_old = PositionView("X", df.index[-3], 96, 90, 96, 10)
    assert st.exit(ind, df.index[-3], pos_old) is not None  # time stop


def breakout_frame():
    closes = list(np.linspace(50, 80, 200)) + [79 - (i % 5) * 0.5 for i in range(60)] + [84.0]
    vol = [1_000_000] * (len(closes) - 1) + [2_500_000]
    return make_frame(closes, volume=vol)


def test_breakout_entry_needs_volume():
    st = BreakoutMomentum()
    df = breakout_frame()
    ind = IndicatorTable(st.prepare(df))
    sig = st.entry("X", ind, df.index[-1], True)
    assert sig is not None and any("Volume" in r for r in sig.reasons)
    quiet = df.copy()
    quiet.iloc[-1, quiet.columns.get_loc("volume")] = 1_000_000
    assert st.entry("X", IndicatorTable(st.prepare(quiet)), quiet.index[-1], True) is None


def test_breakout_lookahead_safe_prior_high_excludes_today():
    st = BreakoutMomentum()
    df = breakout_frame()
    prepared = st.prepare(df)
    assert prepared["prior_high"].iloc[-1] < df["close"].iloc[-1]


def test_trailing_stop_only_rises():
    st = BreakoutMomentum()
    df = breakout_frame()
    ind = IndicatorTable(st.prepare(df))
    day = df.index[-1]
    low_stop = PositionView("X", df.index[-10], 80, 70, 84, 9)
    new = st.trail_stop(ind, day, low_stop)
    assert new is not None and new > 70
    high_stop = PositionView("X", df.index[-10], 80, 83.9, 84, 9)
    assert st.trail_stop(ind, day, high_stop) is None


def test_regime_filter():
    up = make_frame(list(np.linspace(50, 100, 250)))
    down = make_frame(list(np.linspace(100, 50, 250)))
    assert RegimeFilter(up).risk_on(up.index[-1])
    assert not RegimeFilter(down).risk_on(down.index[-1])
    assert not RegimeFilter(up).risk_on(up.index[10])   # SMA not defined yet -> no entries


def test_params_validated():
    import pytest
    with pytest.raises(ValueError):
        TrendPullback({"rsi_entry": 99})
    assert TrendPullback({"rsi_entry": 5}).params["rsi_entry"] == 5
