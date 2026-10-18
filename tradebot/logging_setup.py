"""Structured (JSON-lines) logging with secret redaction."""

from __future__ import annotations

import json
import logging
import logging.handlers
import re
from datetime import datetime, timezone

from . import paths
from .config import secret_values

_PATTERNS = [
    re.compile(r"(api_token=)[^&\s]+", re.I),
    re.compile(r"(api[_-]?key[\"']?\s*[:=]\s*[\"']?)[^&\s\"']+", re.I),
    re.compile(r"(password[\"']?\s*[:=]\s*[\"']?)[^&\s\"']+", re.I),
    re.compile(r"(authorization[\"']?\s*[:=]\s*[\"']?)[^\s\"']+", re.I),
    re.compile(r"(token[\"']?\s*[:=]\s*[\"']?)[A-Za-z0-9._\-]{12,}", re.I),
]


def redact(text: str) -> str:
    for val in secret_values():
        text = text.replace(val, "***REDACTED***")
    for pat in _PATTERNS:
        text = pat.sub(r"\1***REDACTED***", text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        record.msg = redact(msg)
        record.args = None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("event", "symbol", "order_id", "account_id"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


_configured = False


def setup_logging(level: int = logging.INFO, console: bool = True) -> None:
    global _configured
    if _configured:
        return
    paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level)
    fh = logging.handlers.RotatingFileHandler(
        paths.LOG_DIR / "tradebot.log", maxBytes=5_000_000, backupCount=10, encoding="utf-8"
    )
    fh.setFormatter(JsonFormatter())
    fh.addFilter(RedactingFilter())
    root.addHandler(fh)
    if console:
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
        ch.addFilter(RedactingFilter())
        root.addHandler(ch)
    for noisy in ("httpx", "httpcore", "yfinance", "urllib3", "peewee"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True
