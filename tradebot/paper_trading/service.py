"""Local paper trading service: the daily cycle, controls and account reset."""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import date, datetime

from ..brokers.live_guard import order_capable_mode
from ..brokers.paper import LocalPaperBroker
from ..config import load_universe
from ..core.dataview import StoreDataView
from ..core.trading import TradingCore
from ..costs import CostModel
from ..data import calendar as cal
from ..data.store import MarketData
from ..db import Database
from ..notifications import Alerts
from ..portfolio.ledger import Ledger
from ..risk.manager import RiskManager
from ..strategies import build_strategies

log = logging.getLogger(__name__)
MODE = "local_paper"


class TradingService:
    def __init__(self, db: Database, settings: dict, market: MarketData, alerts: Alerts):
        self.db = db
        self.settings = settings
        self.market = market
        self.alerts = alerts
        self.ledger = Ledger(db)
        self._lock = threading.Lock()
        self.last_cycle: dict | None = None

    # ---------------------------------------------------------------- account
    def account(self) -> dict:
        acc = self.ledger.active_account(MODE)
        if acc is None:
            p = self.settings["paper"]
            aid = self.ledger.create_account(MODE, "Local paper account", float(p["starting_capital"]),
                                             p["cost_profile"], on=cal.last_completed_session())
            self.alerts.raise_("info", "account", f"Created simulated paper account with £{p['starting_capital']:,.2f}")
            acc = self.ledger.active_account(MODE)
            assert acc and acc["id"] == aid
        return acc

    def broker(self) -> LocalPaperBroker:
        acc = self.account()
        return LocalPaperBroker(self.db, acc["id"], CostModel(self.settings, acc["cost_profile"]), self.settings)

    def symbols(self) -> list[str]:
        syms = [i["symbol"] for i in self.market.instruments()]
        bench = self.settings["universe"]["benchmark"]
        return syms if bench in syms else syms + [bench]

    def core(self, now: datetime | None = None, view=None) -> TradingCore:
        broker = self.broker()
        view = view or StoreDataView(self.market, self.symbols(), int(self.settings["data"]["stale_after_sessions"]), now)
        costs = broker.costs
        return TradingCore(self.db, self.settings, view, broker, build_strategies(self.settings),
                           RiskManager(self.settings, costs), self.alerts, mode=MODE)

    # ---------------------------------------------------------------- cycle
    def _run_marker(self, account_id: int, kind: str, day: date) -> bool:
        try:
            self.db.execute("INSERT INTO runs(account_id,kind,run_date,started_at,status) VALUES (?,?,?,?, 'running')",
                            (account_id, kind, day.isoformat(), datetime.now().isoformat(timespec="seconds")))
            return True
        except sqlite3.IntegrityError:
            return False

    def _finish_marker(self, account_id: int, kind: str, day: date, status: str, msg: str = "") -> None:
        self.db.execute("UPDATE runs SET finished_at=?, status=?, message=? WHERE account_id=? AND kind=? AND run_date=?",
                        (datetime.now().isoformat(timespec="seconds"), status, msg[:1000], account_id, kind, day.isoformat()))

    def run_daily_cycle(self, now: datetime | None = None, update_data: bool = True, progress=None) -> dict:
        """Safe to call repeatedly: each session is processed at most once."""
        order_capable_mode(self.settings)  # refuses anything but local paper
        if not self._lock.acquire(blocking=False):
            return {"ok": False, "message": "A daily cycle is already running"}
        try:
            return self._cycle(now or cal.now_london(), update_data, progress)
        finally:
            self._lock.release()

    def _cycle(self, now: datetime, update_data: bool, progress) -> dict:
        acc = self.account()
        aid = acc["id"]
        summary: dict = {"ok": True, "account_id": aid, "started": now.isoformat(), "processed_sessions": [],
                         "decided_for": None, "orders": [], "signals": 0, "blocks": [], "data": {}}
        try:
            self.market.sync_instruments(load_universe(self.settings))
            if update_data:
                results = self.market.update_all(self.symbols(), now, progress)
                failed = [r.symbol for r in results if not r.ok]
                summary["data"] = {"updated": len(results) - len(failed), "failed": failed}
                for r in results:
                    if r.restated:
                        self.alerts.raise_("warning", "data", f"{r.symbol}: price history was restated by the source "
                                                              "(possible split/adjustment) and reloaded")
                if failed:
                    self.alerts.raise_("warning", "data", f"Price update failed for {len(failed)} instrument(s): "
                                                          f"{', '.join(failed[:10])}. They cannot be traded until fixed.")
            core = self.core(now)
            core.prepare(self.symbols())
            bench = self.settings["universe"]["benchmark"]
            hist = core.data.history(bench)
            if hist.empty:
                raise RuntimeError(f"no data for benchmark {bench} - run a data update first")
            latest = hist.index[-1]
            expected = cal.last_completed_session(now)
            if latest < expected:
                stale = (f"Latest {bench} data is for {latest} but {expected} is expected: data is stale, "
                         "so no new entries can be made until prices update")
                summary["blocks"].append(stale)
                self.alerts.raise_("warning", "data", stale)
            last_row = self.db.one("SELECT MAX(run_date) FROM runs WHERE account_id=? AND kind='session' AND status='ok'", (aid,))
            last_done = date.fromisoformat(last_row[0]) if last_row and last_row[0] else None
            todo = [d for d in hist.index if (last_done is None and d == latest) or (last_done is not None and last_done < d <= latest)]
            for d in todo:
                if self._run_marker(aid, "session", d):
                    core.process_session(d)
                    self._finish_marker(aid, "session", d, "ok")
                    summary["processed_sessions"].append(d.isoformat())
            if self._run_marker(aid, "decide", latest):
                allow = latest == expected
                today = now.date()
                order_date = today if (cal.is_trading_day(today) and now >= cal.session_open(today) and latest < today) else latest
                res = core.decide(latest, allow_entries=allow, order_date=order_date)
                self._finish_marker(aid, "decide", latest, "ok", "; ".join(res.blocks))
                summary.update(decided_for=latest.isoformat(), orders=[o["client_order_id"] for o in res.orders],
                               signals=len(res.signals), blocks=summary["blocks"] + res.blocks)
                for b in res.blocks:
                    self.alerts.raise_("warning", "trading_block", f"New entries blocked for {latest}: {b}")
            else:
                summary["message"] = f"Decisions for {latest} were already made; nothing new to do."
            summary["reports"] = self._auto_reports(aid, latest)
            self.db.execute("UPDATE risk_state SET consecutive_failures=0 WHERE account_id=?", (aid,))
            self.alerts.raise_("info", "cycle", f"Daily paper cycle complete for {latest}: "
                                                f"{len(summary['orders'])} order(s) placed, {summary['signals']} signal(s).")
        except Exception as exc:
            log.exception("daily cycle failed")
            summary.update(ok=False, message=f"Daily cycle failed: {type(exc).__name__}: {exc}")
            self.db.execute("DELETE FROM runs WHERE account_id=? AND status='running'", (aid,))
            self.db.execute("UPDATE risk_state SET consecutive_failures=consecutive_failures+1 WHERE account_id=?", (aid,))
            rs = self.db.one("SELECT consecutive_failures FROM risk_state WHERE account_id=?", (aid,))
            thr = int(self.settings["risk"]["failure_pause_threshold"])
            self.alerts.raise_("critical", "cycle", summary["message"])
            if rs and rs[0] >= thr:
                self.db.execute("UPDATE risk_state SET paused_reason=? WHERE account_id=?",
                                (f"Paused after {rs[0]} consecutive cycle failures - check the logs, then resume.", aid))
                self.alerts.raise_("critical", "risk", f"Trading paused after {rs[0]} consecutive failures.")
        self.last_cycle = summary
        return summary

    def _auto_reports(self, aid: int, day: date) -> list[str]:
        """Daily report every cycle; weekly report on the week's last trading day.
        A reporting problem is logged but never fails the trading cycle."""
        from ..reports.generator import generate
        made = []
        kinds = ["daily"]
        if cal.next_trading_day(day).isocalendar()[1] != day.isocalendar()[1]:
            kinds.append("weekly")
        for kind in kinds:
            try:
                res = generate(self.db, aid, kind, day, self.settings["universe"]["benchmark"], self.market)
                made.append(res["path"])
                if kind == "weekly":
                    self.alerts.raise_("info", "report", f"Weekly report ready for the week ending {day}")
            except Exception:
                log.exception("%s report failed", kind)
                self.alerts.raise_("warning", "report", f"Could not generate the {kind} report for {day} (see log)")
        return made

    # ---------------------------------------------------------------- controls
    def set_kill_switch(self, on: bool) -> None:
        self.core().set_kill_switch(on)

    def resume(self) -> None:
        self.core().resume_from_pause()

    def flatten_all(self) -> list[dict]:
        today = cal.now_london().date()
        return self.core().flatten_all(today)

    def reset_account(self, confirmation: str) -> dict:
        """Archive (never delete) the current paper account after exporting it."""
        if confirmation != "RESET":
            raise ValueError("Type RESET to confirm")
        from ..reports.export import export_account
        acc = self.account()
        files = export_account(self.db, acc["id"], label=f"before-reset-account-{acc['id']}")
        self.ledger.archive_account(acc["id"])
        for o in self.db.query("SELECT id FROM orders WHERE account_id=? AND status IN ('PENDING','PARTIAL')", (acc["id"],)):
            LocalPaperBroker(self.db, acc["id"], CostModel(self.settings, acc["cost_profile"]), self.settings) \
                .cancel_order(o["id"], "account reset by user")
        new = self.account()
        self.alerts.raise_("warning", "account", f"Paper account {acc['id']} archived (exported to {files['folder']}); "
                                                 f"new account {new['id']} created.")
        return {"archived": acc["id"], "new": new["id"], "export": files}
