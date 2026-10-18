"""Test fixtures. Every test runs in a throw-away project folder and never
touches the network, your real database or your settings."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parent.parent
_TMP = Path(tempfile.mkdtemp(prefix="tradebot-tests-"))
shutil.copytree(REPO / "config", _TMP / "config", ignore=shutil.ignore_patterns("settings.yaml"))
os.environ["TRADEBOT_HOME"] = str(_TMP)
sys.path.insert(0, str(REPO))

from tradebot import paths  # noqa: E402
from tradebot.data.calendar import LONDON  # noqa: E402

TEST_UNIVERSE = """
instruments:
  - {symbol: ISF.L, name: "FTSE 100 ETF", type: etf, sector: "UK Equity ETF", tier: etf}
  - {symbol: AAA.L, name: "Alpha", type: stock, sector: "Financials", tier: large_cap}
  - {symbol: BBB.L, name: "Beta", type: stock, sector: "Energy", tier: large_cap}
  - {symbol: CCC.L, name: "Gamma", type: stock, sector: "Health Care", tier: large_cap}
  - {symbol: DDD.L, name: "Delta", type: etf, sector: "US Equity ETF", tier: etf}
  - {symbol: EEE.L, name: "Epsilon", type: stock, sector: "Industrials", tier: large_cap}
"""


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Point every path at a fresh folder for each test."""
    home = tmp_path / "home"
    shutil.copytree(REPO / "config", home / "config", ignore=shutil.ignore_patterns("settings.yaml"))
    (home / "config" / "test_universe.yaml").write_text(TEST_UNIVERSE, encoding="utf-8")
    for name, rel in [("PROJECT_ROOT", ""), ("CONFIG_DIR", "config"), ("LOG_DIR", "logs"), ("DB_DIR", "database"),
                      ("BACKUP_DIR", "backups"), ("EXPORT_DIR", "exports")]:
        monkeypatch.setattr(paths, name, home / rel if rel else home)
    monkeypatch.setattr(paths, "DEFAULT_SETTINGS_FILE", home / "config" / "default_settings.yaml")
    monkeypatch.setattr(paths, "USER_SETTINGS_FILE", home / "config" / "settings.yaml")
    monkeypatch.setattr(paths, "ENV_FILE", home / ".env")
    monkeypatch.setattr(paths, "DB_FILE", home / "database" / "tradebot.sqlite3")
    monkeypatch.setattr(paths, "LOCK_FILE", home / "database" / "tradebot.lock")
    monkeypatch.setattr(paths, "CONTROL_TOKEN_FILE", home / "database" / "control.token")
    paths.ensure_dirs()
    yield home


SYNTH = {"data": {"provider": "synthetic", "fallback_provider": None},
         "universe": {"file": "config/test_universe.yaml", "min_avg_daily_value_gbp": 0},
         "ai": {"enabled": False}}


@pytest.fixture
def settings():
    from tradebot.config import load_settings
    return load_settings(overrides=SYNTH)


@pytest.fixture
def ctx(isolated_home):
    from tradebot.runtime import AppContext
    c = AppContext(settings_overrides=SYNTH)
    yield c
    c.stop()
    c.db.close()


def london(y, m, d, hh=19, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=LONDON)


def make_frame(closes, start=date(2020, 1, 2), volume=1_000_000, spread=0.01, opens=None, highs=None, lows=None):
    """Daily bars on consecutive LSE trading days from explicit closes."""
    from tradebot.data.calendar import next_trading_day, is_trading_day
    d = start
    while not is_trading_day(d):
        d = next_trading_day(d)
    days = []
    for _ in closes:
        days.append(d)
        d = next_trading_day(d)
    closes = [float(c) for c in closes]
    o = opens or [closes[0]] + closes[:-1]
    h = highs or [max(a, b) * (1 + spread) for a, b in zip(o, closes)]
    l_ = lows or [min(a, b) * (1 - spread) for a, b in zip(o, closes)]
    vol = volume if isinstance(volume, list) else [volume] * len(closes)
    return pd.DataFrame({"open": o, "high": h, "low": l_, "close": closes, "volume": vol},
                        index=pd.Index(days, name="date"))
