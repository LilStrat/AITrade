"""Alerts: stored in the database (shown on the dashboard), written to the
log, and optionally emailed. Wording distinguishes proposals from fills."""

from __future__ import annotations

import logging
import smtplib
import ssl
import threading
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from .config import get_secret
from .db import Database

log = logging.getLogger("tradebot.alerts")
LEVELS = {"info": 0, "warning": 1, "critical": 2}


class Alerts:
    def __init__(self, db: Database | None, settings: dict | None = None):
        self.db = db
        self.settings = settings or {}

    def raise_(self, level: str, category: str, message: str, dedupe_hours: float = 12) -> None:
        if level not in LEVELS:
            level = "warning"
        getattr(log, "critical" if level == "critical" else level)("[%s] %s", category, message)
        if self.db is None:
            return
        since = (datetime.now(timezone.utc) - timedelta(hours=dedupe_hours)).isoformat()
        if self.db.one("SELECT 1 FROM alerts WHERE category=? AND message=? AND ts>=?", (category, message, since)):
            return
        self.db.execute("INSERT INTO alerts(ts,level,category,message) VALUES (?,?,?,?)",
                        (datetime.now(timezone.utc).isoformat(timespec="seconds"), level, category, message[:2000]))
        self._maybe_email(level, category, message)

    def _maybe_email(self, level: str, category: str, message: str) -> None:
        n = self.settings.get("notifications", {})
        if not n.get("email_enabled"):
            return
        if LEVELS[level] < LEVELS.get(n.get("email_min_level", "warning"), 1):
            return
        threading.Thread(target=send_email, args=(f"[Trading bot] {level.upper()}: {category}", message),
                         daemon=True).start()

    def recent(self, limit: int = 50, unacknowledged_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM alerts" + (" WHERE acknowledged=0" if unacknowledged_only else "") + " ORDER BY id DESC LIMIT ?"
        return [dict(r) for r in self.db.query(sql, (limit,))] if self.db else []

    def acknowledge_all(self) -> None:
        if self.db:
            self.db.execute("UPDATE alerts SET acknowledged=1 WHERE acknowledged=0")


class NullAlerts(Alerts):
    """Used by the backtester: collects messages in memory only."""

    def __init__(self):
        super().__init__(None)
        self.messages: list[tuple[str, str, str]] = []

    def raise_(self, level: str, category: str, message: str, dedupe_hours: float = 12) -> None:
        self.messages.append((level, category, message))


def send_email(subject: str, body: str) -> bool:
    """Send via SMTP using credentials from .env only (never settings files)."""
    host, user, pwd = get_secret("SMTP_HOST"), get_secret("SMTP_USER"), get_secret("SMTP_PASSWORD")
    to = get_secret("ALERT_EMAIL_TO")
    if not all((host, user, pwd, to)):
        log.warning("email alert skipped: SMTP settings incomplete in .env")
        return False
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.set_content(body + "\n\n-- Sent by your local trading bot (paper mode unless stated otherwise).")
    try:
        port = int(get_secret("SMTP_PORT") or 587)
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(user, pwd)
            s.send_message(msg)
        return True
    except Exception as exc:
        log.warning("email alert failed: %s", type(exc).__name__)
        return False
