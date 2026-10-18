"""Application context: builds and holds the long-lived components."""

from __future__ import annotations

import logging
import threading
from datetime import datetime

from . import paths
from .ai.analyst import Analyst
from .config import ensure_user_settings, load_env_file, load_settings, load_universe
from .data import calendar as cal
from .data.store import MarketData
from .db import Database
from .notifications import Alerts
from .paper_trading.service import TradingService

log = logging.getLogger(__name__)


class AppContext:
    def __init__(self, db_path=None, settings_overrides: dict | None = None):
        paths.ensure_dirs()
        load_env_file()
        ensure_user_settings()
        self._overrides = settings_overrides
        self.settings = load_settings(overrides=settings_overrides)
        self.db = Database(db_path or paths.DB_FILE)
        self.alerts = Alerts(self.db, self.settings)
        self.market = MarketData(self.db, self.settings)
        self.market.sync_instruments(load_universe(self.settings))
        self.service = TradingService(self.db, self.settings, self.market, self.alerts)
        self.analyst = Analyst(self.db, self.settings)
        self.started_at = datetime.now()
        self.background: dict[str, dict] = {}   # name -> {status, message}
        self._stop = threading.Event()
        self._scheduler: threading.Thread | None = None

    def reload_settings(self) -> None:
        """Re-read settings after a change (components are rebuilt)."""
        self.settings = load_settings(overrides=self._overrides)
        self.alerts.settings = self.settings
        self.market = MarketData(self.db, self.settings)
        self.market.sync_instruments(load_universe(self.settings))
        self.service = TradingService(self.db, self.settings, self.market, self.alerts)
        self.analyst = Analyst(self.db, self.settings)

    # ------------------------------------------------------------- background
    def run_background(self, name: str, fn, *args) -> bool:
        cur = self.background.get(name)
        if cur and cur.get("status") == "running":
            return False
        self.background[name] = {"status": "running", "message": "", "started": datetime.now().isoformat(timespec="seconds")}

        def wrapper():
            try:
                res = fn(*args)
                self.background[name].update(status="done", result=res,
                                             message=(res.get("message") if isinstance(res, dict) else "") or "finished")
            except Exception as exc:
                log.exception("background task %s failed", name)
                self.background[name].update(status="failed", message=f"{type(exc).__name__}: {exc}")
                self.alerts.raise_("warning", "background", f"{name} failed: {type(exc).__name__}: {exc}")

        threading.Thread(target=wrapper, name=name, daemon=True).start()
        return True

    def start_scheduler(self) -> None:
        if self._scheduler is not None:
            return
        self._scheduler = threading.Thread(target=self._scheduler_loop, name="scheduler", daemon=True)
        self._scheduler.start()

    def _scheduler_loop(self) -> None:
        """While the app is open and auto_daily_cycle is ON, run the paper
        cycle once per completed session after the configured time."""
        while not self._stop.wait(60):
            try:
                if not self.settings["app"].get("auto_daily_cycle"):
                    continue
                if self.settings["app"]["mode"] != "local_paper":
                    continue
                now = cal.now_london()
                hh, mm = (int(x) for x in str(self.settings["app"]["daily_cycle_time"]).split(":"))
                if not cal.is_trading_day(now.date()) or (now.hour, now.minute) < (hh, mm):
                    continue
                acc = self.service.account()
                done = self.db.one("SELECT 1 FROM runs WHERE account_id=? AND kind='decide' AND run_date=?",
                                   (acc["id"], now.date().isoformat()))
                tried = self.background.get("daily_cycle", {})
                if done or tried.get("status") == "running" or tried.get("for") == now.date().isoformat():
                    continue
                self.run_background("daily_cycle", self.service.run_daily_cycle)
                self.background["daily_cycle"]["for"] = now.date().isoformat()
            except Exception:
                log.exception("scheduler tick failed")

    def stop(self) -> None:
        self._stop.set()
