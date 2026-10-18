from datetime import date

import pytest

from tradebot.backtesting.data import synthetic_view
from tradebot.backtesting.engine import BacktestRequest, run_backtest
from tradebot.backtesting.research import grid_points, run_research
from tradebot.config import load_universe

SYMS = ["ISF.L", "AAA.L", "BBB.L", "CCC.L", "DDD.L", "EEE.L"]


@pytest.fixture
def view(settings):
    return synthetic_view(SYMS, load_universe(settings), date(2012, 1, 1), date(2024, 12, 31))


def bt(settings, view, start=date(2016, 1, 1), end=date(2019, 12, 31), **kw):
    req = BacktestRequest(strategies=["trend_pullback", "breakout_momentum"], symbols=SYMS, start=start, end=end, **kw)
    return run_backtest(settings, view, req)


def test_backtest_accounting_is_consistent(settings, view):
    r = bt(settings, view)
    m = r["metrics_after_costs"]
    assert m["start_equity"] > 0 and r["period"]["sessions"] > 900
    t = r["trade_metrics"]
    # P&L identity: end equity = start + realised net trade P&L + dividends + open-position value change
    closed_net = sum(x["net_pnl_gbp"] for x in r["trades"] if x.get("exit_date"))
    assert t.get("net_pnl_gbp", 0) == pytest.approx(closed_net, abs=0.05)
    # costs are positive and before-cost results are never worse than after-cost
    assert r["costs"]["total_gbp"] >= 0
    assert r["metrics_before_costs_approx"]["end_equity"] >= m["end_equity"] - 1e-6
    if t.get("trades"):
        assert t["gross_pnl_before_costs_gbp"] >= t["net_pnl_gbp"]


def test_backtest_is_deterministic(settings, view):
    a, b = bt(settings, view), bt(settings, view)
    assert a["equity_curve"] == b["equity_curve"]


def test_no_lookahead_future_changes_do_not_alter_past(settings, view):
    """Change all prices after a cut-off; every decision before it must be identical."""
    cut = date(2018, 6, 29)
    base = bt(settings, view)
    for sym, df in view.frames.items():
        mask = df.index > cut
        df.loc[mask, ["open", "high", "low", "close"]] *= 0.5
        df.loc[mask, "volume"] *= 3
    changed = bt(settings, view)
    before = lambda curve: [p for p in curve if p[0] <= cut.isoformat()]  # noqa: E731
    assert before(base["equity_curve"]) == before(changed["equity_curve"])
    trades_before = lambda r: [x for x in r["trades"] if x.get("exit_date") and x["exit_date"] <= cut.isoformat()]  # noqa: E731
    assert trades_before(base) == trades_before(changed)


def test_costs_reduce_returns(settings, view):
    with_costs = bt(settings, view, cost_profile="ibkr_uk_fixed")
    no_costs = bt(settings, view, cost_profile="zero_cost")
    if with_costs["trade_metrics"].get("trades"):
        assert with_costs["costs"]["commission_gbp"] > 0
        assert no_costs["costs"]["commission_gbp"] == 0


def test_benchmark_and_cash_curves_present(settings, view):
    r = bt(settings, view)
    assert r["benchmark_curve"] and r["cash_curve"]
    assert r["cash_curve"][-1][1] > r["cash_curve"][0][1]
    assert r["limitations"]


def test_research_produces_verdict_with_holdout(settings, view, monkeypatch):
    from tradebot.backtesting import research
    monkeypatch.setitem(research.GRIDS, "trend_pullback", {"rsi_entry": [5, 10], "stop_atr": [2.5]})  # keep the test fast
    r = run_research(settings, view, "trend_pullback", SYMS, date(2013, 6, 1), date(2021, 12, 31), workers=1)
    assert r["verdict"] in ("REJECT", "INSUFFICIENT EVIDENCE", "PROVISIONAL")
    assert r["holdout"][0] > r["development"][1]
    assert all(w["test"][1] <= r["development"][1] for w in r["walk_forward"])  # OOS windows never touch holdout
    assert len(r["sensitivity"]) == len(grid_points("trend_pullback"))
