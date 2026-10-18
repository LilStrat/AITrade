import pytest

from tradebot.costs import CostModel
from tradebot.execution import simulate_market, simulate_sell_limit, simulate_sell_stop

STOCK = {"type": "stock", "exchange": "LSE", "tier": "large_cap"}
ETF = {"type": "etf", "exchange": "LSE", "tier": "etf"}
US = {"type": "stock", "exchange": "US", "tier": "large_cap"}


def test_ibkr_fixed_commission(settings):
    c = CostModel(settings, "ibkr_uk_fixed")
    assert c.fees("BUY", 100, 10.0, "GBP", 1.0, ETF).commission_gbp == 3.00      # £1,000 -> £3 minimum
    assert c.fees("BUY", 1000, 10.0, "GBP", 1.0, ETF).commission_gbp == 5.00     # £10,000 x 0.05%


def test_stamp_duty_and_ptm_levy(settings):
    c = CostModel(settings, "ibkr_uk_fixed")
    buy = c.fees("BUY", 1000, 10.0, "GBP", 1.0, STOCK)
    assert buy.stamp_duty_gbp == 50.00 and buy.other_fees_gbp == 0          # exactly £10,000: no PTM
    big = c.fees("BUY", 1100, 10.0, "GBP", 1.0, STOCK)
    assert big.other_fees_gbp == 1.50
    assert c.fees("SELL", 1000, 10.0, "GBP", 1.0, STOCK).stamp_duty_gbp == 0  # no stamp duty on sales
    assert c.fees("BUY", 1000, 10.0, "GBP", 1.0, ETF).stamp_duty_gbp == 0     # ETFs exempt


def test_usd_commission_and_fx_fee(settings):
    c = CostModel(settings, "ibkr_uk_fixed")
    f = c.fees("BUY", 10, 400.0, "USD", 0.8, US)          # $4,000 = £3,200
    assert f.commission_gbp == pytest.approx(0.80)        # $1 minimum x 0.8
    assert f.fx_cost_gbp == pytest.approx(3200 * 0.0003, abs=0.01)
    assert f.stamp_duty_gbp == 0
    t = CostModel(settings, "trading212").fees("BUY", 10, 400.0, "USD", 0.8, US)
    assert t.commission_gbp == 0 and t.fx_cost_gbp == pytest.approx(4.80)


def test_round_trip_includes_spread(settings):
    c = CostModel(settings, "zero_cost")
    rt = c.round_trip_estimate(100, 10.0, "GBP", 1.0, ETF)
    assert rt == pytest.approx(2 * 1000 * (5 + 5) / 10_000)  # spread 5bp + slippage 5bp, both ways


BAR = {"open": 100.0, "high": 105.0, "low": 95.0, "close": 102.0, "volume": 10_000}


def test_market_fill_at_open_with_impact_and_volume_cap():
    f = simulate_market("BUY", 100, BAR, 10, 5)
    assert f.price == pytest.approx(100.1) and f.quantity == 100
    s = simulate_market("SELL", 100, BAR, 10, 5)
    assert s.price == pytest.approx(99.9)
    capped = simulate_market("BUY", 2000, BAR, 10, 5)   # 5% of 10,000 = 500
    assert capped.quantity == 500 and "partial" in capped.note
    assert simulate_market("BUY", 10, {**BAR, "volume": 0}, 10, 5) is None


def test_stop_gap_fills_at_worse_open():
    gap = {"open": 90.0, "high": 92.0, "low": 88.0, "close": 91.0, "volume": 1000}
    f = simulate_sell_stop(10, 95.0, gap, 15, 5)
    assert f.price < 95.0 and f.reference_price == 90.0 and "GAP" in f.note
    assert f.price == pytest.approx(90 * (1 - 0.0015))


def test_stop_touched_intraday_and_not_triggered():
    f = simulate_sell_stop(10, 96.0, BAR, 15, 5)
    assert f.reference_price == 96.0 and f.price == pytest.approx(96 * (1 - 0.0015))
    assert simulate_sell_stop(10, 90.0, BAR, 15, 5) is None


def test_stop_never_fills_below_bar_low():
    bar = {"open": 100.0, "high": 101.0, "low": 95.99, "close": 100.0, "volume": 1000}
    f = simulate_sell_stop(10, 96.0, bar, 100, 5)
    assert f.price >= bar["low"]


def test_limit_requires_trading_through():
    assert simulate_sell_limit(10, 105.0, BAR, 5, 5) is None          # high == limit, not through
    f = simulate_sell_limit(10, 104.0, BAR, 5, 5)
    assert f.price == 104.0
    up = {**BAR, "open": 106.0, "high": 107.0}
    assert simulate_sell_limit(10, 104.0, up, 5, 5).price >= 104.0
