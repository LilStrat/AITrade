import pytest

from tradebot.config import ConfigError, load_settings
from tradebot.costs import CostModel
from tradebot.risk.manager import HeldItem, Proposal, RiskContext, RiskManager

ETF = {"symbol": "XXX.L", "type": "etf", "exchange": "LSE", "tier": "etf", "sector": "Test"}


def prop(**kw):
    base = dict(symbol="XXX.L", strategy="t", side="BUY", entry_est=10.0, stop_distance=0.5, currency="GBP",
                fx_to_gbp=1.0, instrument=ETF, avg_volume_20=1_000_000, avg_value_gbp_20=10_000_000, sector="Test")
    base.update(kw)
    return Proposal(**base)


def ctx(**kw):
    base = dict(mode="local_paper", equity_gbp=10_000, cash_gbp=10_000, reserved_gbp=0, held=[], pending=[],
                global_blocks=[], symbol_data_fresh=True, symbol_data_note="fresh")
    base.update(kw)
    return RiskContext(**base)


@pytest.fixture
def rm():
    s = load_settings()  # shipped defaults, including the liquidity minimum
    return RiskManager(s, CostModel(s, "ibkr_uk_fixed"))


def test_position_size_from_stop_distance(rm):
    d = rm.evaluate(prop(), ctx())
    # 0.5% of £10,000 = £50 risk / £0.50 stop = 100 shares
    assert d.approved and d.quantity == 100 and d.risk_gbp == pytest.approx(50)


def test_max_position_cap_binds(rm):
    # £20,000 equity: risk allows 500 shares (£100 / £0.20) but 20% cap = £4,000 = 400 shares
    d = rm.evaluate(prop(stop_distance=0.2), ctx(equity_gbp=20_000, cash_gbp=20_000))
    assert d.approved and d.quantity == 400


def test_cash_and_liquidity_caps(rm):
    d = rm.evaluate(prop(), ctx(cash_gbp=600))
    assert d.quantity <= 55
    d2 = rm.evaluate(prop(avg_volume_20=3000), ctx())  # 1% of 3,000 = 30 shares
    assert d2.quantity == 30 or not d2.approved


def test_tight_stop_rejected_when_costs_dominate(rm):
    # 1.5% stop on a £2,000 position risks only £30; ~£10 round-trip costs = 33% > 20% limit
    d = rm.evaluate(prop(stop_distance=0.15), ctx())
    assert not d.approved and any("round-trip costs" in r for r in d.rejections)


def test_rejections(rm):
    assert not rm.evaluate(prop(), ctx(symbol_data_fresh=False, symbol_data_note="stale")).approved
    assert not rm.evaluate(prop(), ctx(global_blocks=["EMERGENCY STOP is active"])).approved
    assert not rm.evaluate(prop(), ctx(held=[HeldItem("XXX.L", 1000, "Test")])).approved            # duplicate
    assert not rm.evaluate(prop(), ctx(pending=[HeldItem("XXX.L", 1000, "Test")])).approved
    assert not rm.evaluate(prop(side="SELL"), ctx()).approved                                       # no shorts
    assert not rm.evaluate(prop(instrument={**ETF, "type": "cfd"}), ctx()).approved
    assert not rm.evaluate(prop(fx_to_gbp=None, currency="USD"), ctx()).approved
    assert not rm.evaluate(prop(stop_distance=0.01), ctx()).approved                                # stop 0.1% too tight
    assert not rm.evaluate(prop(stop_distance=5.0), ctx()).approved                                 # stop 50% too wide
    assert not rm.evaluate(prop(), ctx(mode="live")).approved
    assert not rm.evaluate(prop(avg_value_gbp_20=10), ctx()).approved                               # illiquid


def test_portfolio_level_limits(rm):
    six = [HeldItem(f"H{i}.L", 500, f"S{i}") for i in range(6)]
    assert not rm.evaluate(prop(), ctx(held=six)).approved                                          # max positions
    heavy = [HeldItem("A.L", 9000, "Other")]
    d = rm.evaluate(prop(), ctx(held=heavy, cash_gbp=1000, equity_gbp=10_000))
    assert not d.approved and any("Exposure" in r or "Size 0" in r for r in d.rejections)
    sector = [HeldItem("A.L", 3500, "Test")]
    d = rm.evaluate(prop(), ctx(held=sector))
    assert not d.approved and any("Sector" in r for r in d.rejections)
    corr = rm.evaluate(prop(), ctx(held=[HeldItem("A.L", 100, "x"), HeldItem("B.L", 100, "y")],
                                   correlated_with=["A.L", "B.L"]))
    assert not corr.approved


def test_costs_too_large_relative_to_risk(rm):
    # Tiny account: £3 minimum commission each way dominates a £5 risk budget
    d = rm.evaluate(prop(), ctx(equity_gbp=1000, cash_gbp=1000))
    assert not d.approved and any("round-trip costs" in r for r in d.rejections)


def test_warnings_near_limits(rm):
    five = [HeldItem(f"H{i}.L", 100, f"S{i}") for i in range(4)]
    d = rm.evaluate(prop(), ctx(held=five))
    assert d.approved and d.warnings


def test_short_and_leverage_are_locked():
    with pytest.raises(ConfigError):
        load_settings(overrides={"risk": {"allow_short": True}})
    with pytest.raises(ConfigError):
        load_settings(overrides={"risk": {"allow_leverage": True}})
    with pytest.raises(ConfigError):
        load_settings(overrides={"risk": {"max_total_exposure_pct": 150}})
    with pytest.raises(ConfigError):
        load_settings(overrides={"risk": {"max_risk_per_trade_pct": 10}})
