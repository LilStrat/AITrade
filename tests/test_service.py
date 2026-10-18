"""Paper-trading daily cycle: integration of data, strategies, risk, broker and accounting."""

from datetime import date

import pytest

from tradebot.brokers.live_guard import LiveTradingNotAuthorised
from tradebot.runtime import AppContext
from conftest import SYNTH, london


def run_days(ctx, days):
    out = []
    for d in days:
        out.append(ctx.service.run_daily_cycle(now=london(d.year, d.month, d.day, 19)))
    return out


def trading_days(n, start=date(2026, 6, 1)):
    from tradebot.data.calendar import trading_days_between
    return trading_days_between(start, date(2026, 12, 31))[:n]


def test_daily_cycle_end_to_end(ctx):
    res = run_days(ctx, trading_days(40))
    assert all(r["ok"] for r in res), [r.get("message") for r in res if not r["ok"]]
    aid = res[0]["account_id"]
    snaps = ctx.db.query("SELECT COUNT(*) FROM equity_snapshots WHERE account_id=?", (aid,))[0][0]
    assert snaps == 40
    st = ctx.service.broker().account_state()
    # cash never negative, positions have stops
    assert st.cash_gbp >= 0
    for sym in st.positions:
        stops = [o for o in ctx.service.broker().open_orders(sym) if o["order_type"] == "STOP"]
        assert stops, f"{sym} has no protective stop"


def test_cycle_is_idempotent(ctx):
    d = trading_days(1)[0]
    r1 = ctx.service.run_daily_cycle(now=london(d.year, d.month, d.day, 19))
    n_orders = ctx.db.one("SELECT COUNT(*) FROM orders")[0]
    n_tx = ctx.db.one("SELECT COUNT(*) FROM transactions")[0]
    r2 = ctx.service.run_daily_cycle(now=london(d.year, d.month, d.day, 20))
    assert r1["ok"] and r2["ok"] and "already" in r2.get("message", "")
    assert ctx.db.one("SELECT COUNT(*) FROM orders")[0] == n_orders
    assert ctx.db.one("SELECT COUNT(*) FROM transactions")[0] == n_tx


def test_catch_up_after_pc_was_off_places_no_backdated_entries(ctx):
    days = trading_days(30)
    run_days(ctx, days[:20])
    # PC off for 7 sessions, then run once
    later = days[27]
    r = ctx.service.run_daily_cycle(now=london(later.year, later.month, later.day, 19))
    assert r["ok"] and len(r["processed_sessions"]) == 8
    # new entry orders exist only for the latest decision date
    rows = ctx.db.query("SELECT DISTINCT created_for_date FROM orders WHERE purpose='entry' AND created_for_date>?",
                        (days[19].isoformat(),))
    assert {x[0] for x in rows} <= {later.isoformat()}


def test_stale_data_blocks_new_entries(ctx):
    days = trading_days(5)
    run_days(ctx, days[:4])
    # Next evening, but the data update "fails": run without downloading
    d = days[4]
    r = ctx.service.run_daily_cycle(now=london(d.year, d.month, d.day, 19), update_data=False)
    assert r["ok"]
    assert any("stale" in b.lower() or "not usable" in b.lower() for b in r["blocks"])


def test_repeated_failures_pause_trading(ctx, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("simulated crash")
    monkeypatch.setattr(ctx.service, "core", boom)
    d = trading_days(1)[0]
    for _ in range(3):
        assert not ctx.service.run_daily_cycle(now=london(d.year, d.month, d.day, 19), update_data=False)["ok"]
    acc = ctx.service.account()
    rs = ctx.db.one("SELECT * FROM risk_state WHERE account_id=?", (acc["id"],))
    assert rs["consecutive_failures"] == 3 and "consecutive" in rs["paused_reason"]
    assert ctx.db.one("SELECT COUNT(*) FROM runs WHERE status='running'")[0] == 0  # no half-finished markers


def test_restart_recovery_preserves_state(isolated_home):
    c1 = AppContext(settings_overrides=SYNTH)
    run_days(c1, trading_days(25))
    st1 = c1.service.broker().account_state()
    orders1 = c1.db.one("SELECT COUNT(*) FROM orders")[0]
    c1.db.close()
    c2 = AppContext(settings_overrides=SYNTH)   # "restart"
    st2 = c2.service.broker().account_state()
    assert st2.cash_gbp == st1.cash_gbp and set(st2.positions) == set(st1.positions)
    assert c2.service.account()["id"] == c1.service.account()["id"]
    d = trading_days(25)[-1]
    c2.service.run_daily_cycle(now=london(d.year, d.month, d.day, 21))  # same day again -> nothing new
    assert c2.db.one("SELECT COUNT(*) FROM orders")[0] == orders1
    c2.db.close()


def test_kill_switch_cancels_entries_and_blocks(ctx):
    days = trading_days(30)
    run_days(ctx, days[:29])
    ctx.service.set_kill_switch(True)
    pending_entries = ctx.db.query("SELECT * FROM orders WHERE purpose='entry' AND status='PENDING'")
    assert pending_entries == []
    d = days[29]
    r = ctx.service.run_daily_cycle(now=london(d.year, d.month, d.day, 19))
    assert any("EMERGENCY" in b for b in r["blocks"])
    assert ctx.db.one("SELECT COUNT(*) FROM orders WHERE purpose='entry' AND created_for_date=?", (d.isoformat(),))[0] == 0


def test_reset_archives_and_exports(ctx):
    run_days(ctx, trading_days(5))
    old = ctx.service.account()["id"]
    with pytest.raises(ValueError):
        ctx.service.reset_account("yes")
    res = ctx.service.reset_account("RESET")
    assert res["archived"] == old and res["new"] != old
    from pathlib import Path
    assert (Path(res["export"]["folder"]) / "transactions.csv").exists()
    assert ctx.db.one("SELECT status FROM accounts WHERE id=?", (old,))[0] == "archived"
    assert ctx.db.one("SELECT COUNT(*) FROM transactions WHERE account_id=?", (old,))[0] >= 1  # history kept


def test_live_and_broker_paper_modes_refuse_orders(isolated_home):
    for mode in ("live", "broker_paper"):
        c = AppContext(settings_overrides={**SYNTH, "app": {"mode": mode}})
        with pytest.raises(LiveTradingNotAuthorised):
            c.service.run_daily_cycle(now=london(2026, 6, 1, 19))
        assert c.db.one("SELECT COUNT(*) FROM orders")[0] == 0
        c.db.close()
