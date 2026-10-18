import smtplib

from tradebot import notifications
from tradebot.db import Database
from tradebot.notifications import Alerts, send_email


class FakeSMTP:
    sent = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.tls = self.logged_in = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        self.tls = True

    def login(self, user, pwd):
        assert self.tls, "must use TLS before sending the password"
        self.logged_in = (user, pwd)

    def send_message(self, msg):
        FakeSMTP.sent.append((self, msg))


def test_email_uses_env_credentials_and_tls(monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    for k, v in {"SMTP_HOST": "smtp.example.com", "SMTP_PORT": "587", "SMTP_USER": "me@example.com",
                 "SMTP_PASSWORD": "app-password-123", "ALERT_EMAIL_TO": "me@example.com"}.items():
        monkeypatch.setenv(k, v)
    FakeSMTP.sent.clear()
    assert send_email("[Trading bot] test", "Daily loss limit reached")
    server, msg = FakeSMTP.sent[0]
    assert server.host == "smtp.example.com" and server.tls and server.logged_in[1] == "app-password-123"
    assert "paper mode" in msg.get_content() and "app-password" not in msg.get_content()


def test_email_skipped_when_not_configured(monkeypatch):
    for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "ALERT_EMAIL_TO"):
        monkeypatch.delenv(k, raising=False)
    assert send_email("x", "y") is False


def test_alert_levels_and_dedupe(monkeypatch):
    calls = []
    monkeypatch.setattr(notifications, "send_email", lambda s, b: calls.append(s))
    monkeypatch.setattr(notifications.threading, "Thread",
                        lambda target, args, daemon: type("T", (), {"start": lambda self: target(*args)})())
    db = Database(":memory:")
    a = Alerts(db, {"notifications": {"email_enabled": True, "email_min_level": "warning"}})
    a.raise_("info", "x", "just info")
    a.raise_("critical", "risk", "drawdown limit")
    a.raise_("critical", "risk", "drawdown limit")      # duplicate within 12h -> stored once
    assert db.one("SELECT COUNT(*) FROM alerts")[0] == 2
    assert len(calls) == 1 and "CRITICAL" in calls[0]
