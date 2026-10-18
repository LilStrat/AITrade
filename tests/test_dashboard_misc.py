"""Dashboard pages render real data; settings protection; backup/restore; lock; reports."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from tradebot.dashboard.server import create_app
from conftest import london

PAGES = ["/", "/market", "/opportunities", "/portfolio", "/history", "/strategies", "/risk", "/backtest", "/ai",
         "/reports", "/settings", "/alerts", "/api/status"]


def run_some(ctx, n=30):
    from tradebot.data.calendar import trading_days_between
    for d in trading_days_between(date(2026, 6, 1), date(2026, 12, 31))[:n]:
        ctx.service.run_daily_cycle(now=london(d.year, d.month, d.day, 19))


@pytest.fixture
def client(ctx):
    run_some(ctx)
    app = create_app(ctx)
    return TestClient(app, base_url="http://127.0.0.1", follow_redirects=False), app


def test_all_pages_render_with_real_data(client, ctx):
    c, _ = client
    for p in PAGES:
        r = c.get(p)
        assert r.status_code == 200, p
    home = c.get("/").text
    assert "PAPER TRADING - SIMULATED MONEY" in home
    equity = ctx.db.one("SELECT equity_gbp FROM equity_snapshots ORDER BY date DESC LIMIT 1")[0]
    acc = ctx.service.account()
    assert f"£{ctx.service.broker().account_state().cash_gbp:,.2f}" in home  # actual figure, not a placeholder
    assert "SYNTHETIC" in c.get("/market").text  # data source disclosed


def test_protected_settings_need_confirmation(client, ctx):
    c, app = client
    tok = app.state.csrf
    r = c.post("/action/settings", data={"csrf_token": tok, "risk.max_risk_per_trade_pct": "1.0"})
    assert "protected" in r.headers["location"] and ctx.settings["risk"]["max_risk_per_trade_pct"] == 0.5
    r = c.post("/action/settings", data={"csrf_token": tok, "risk.max_risk_per_trade_pct": "1.0", "confirm_protected": "on"})
    assert ctx.settings["risk"]["max_risk_per_trade_pct"] == 1.0
    r = c.post("/action/settings", data={"csrf_token": tok, "risk.max_risk_per_trade_pct": "50", "confirm_protected": "on"})
    assert "outside" in r.headers["location"] and ctx.settings["risk"]["max_risk_per_trade_pct"] == 1.0
    assert ctx.db.one("SELECT COUNT(*) FROM settings_audit")[0] == 1


def test_kill_switch_via_dashboard(client, ctx):
    c, app = client
    c.post("/action/kill", data={"csrf_token": app.state.csrf, "on": "1"})
    assert ctx.db.one("SELECT kill_switch FROM risk_state")[0] == 1
    assert "EMERGENCY STOP IS ON" in c.get("/").text


def test_strategy_toggle_and_param_validation(client, ctx):
    c, app = client
    tok = app.state.csrf
    c.post("/action/strategy", data={"csrf_token": tok, "key": "trend_pullback", "p_rsi_entry": "10"})
    assert ctx.settings["strategies"]["trend_pullback"]["enabled"] is False
    r = c.post("/action/strategy", data={"csrf_token": tok, "key": "trend_pullback", "enabled": "on", "p_rsi_entry": "99", "confirm": "on"})
    assert "between" in r.headers["location"]


def test_reports_and_export(client, ctx, isolated_home):
    c, app = client
    r = c.post("/action/report", data={"csrf_token": app.state.csrf, "kind": "weekly"})
    assert r.status_code == 303
    files = list((isolated_home / "exports" / "reports").glob("weekly-*.md"))
    assert files and "SIMULATED" in files[0].read_text()
    c.post("/action/export", data={"csrf_token": app.state.csrf})
    assert list((isolated_home / "exports").glob("paper-export-*/tax_disposals_ESTIMATE.csv"))


def test_backup_and_restore_roundtrip(ctx, isolated_home):
    from tradebot import paths
    from tradebot.backup import create_backup, restore_backup
    from tradebot.db import Database
    run_some(ctx, 5)
    n = ctx.db.one("SELECT COUNT(*) FROM transactions")[0]
    f = create_backup(ctx.db)
    ctx.db.close()
    paths.DB_FILE.unlink()
    with pytest.raises(ValueError):
        restore_backup(f, "no")
    restore_backup(f, "RESTORE")
    db = Database(paths.DB_FILE)
    assert db.one("SELECT COUNT(*) FROM transactions")[0] == n
    db.close()


def test_single_instance_lock(isolated_home):
    from tradebot.lock import AlreadyRunning, InstanceLock
    import subprocess, sys, textwrap
    p = isolated_home / "database" / "t.lock"
    a = InstanceLock(p)
    a.acquire()
    code = textwrap.dedent(f"""
        import sys; sys.path.insert(0, {str(__import__('pathlib').Path(__file__).resolve().parent.parent)!r})
        from tradebot.lock import InstanceLock, AlreadyRunning
        try:
            InstanceLock(__import__('pathlib').Path({str(p)!r})).acquire(); print('acquired')
        except AlreadyRunning: print('blocked')
    """)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout.strip()
    assert out == "blocked"
    a.release()
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout.strip()
    assert out == "acquired"


def test_config_save_stores_only_differences(isolated_home):
    import yaml
    from tradebot.config import load_settings, save_user_settings
    s = load_settings()
    s["risk"]["max_open_positions"] = 4
    f = save_user_settings(s)
    stored = yaml.safe_load(f.read_text())
    assert stored == {"risk": {"max_open_positions": 4}}
