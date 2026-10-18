"""Filesystem locations. Everything lives inside the project folder."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(os.environ.get("TRADEBOT_HOME", Path(__file__).resolve().parent.parent))
CONFIG_DIR = PROJECT_ROOT / "config"
LOG_DIR = PROJECT_ROOT / "logs"
DB_DIR = PROJECT_ROOT / "database"
BACKUP_DIR = PROJECT_ROOT / "backups"
EXPORT_DIR = PROJECT_ROOT / "exports"
DEFAULT_SETTINGS_FILE = CONFIG_DIR / "default_settings.yaml"
USER_SETTINGS_FILE = CONFIG_DIR / "settings.yaml"
ENV_FILE = PROJECT_ROOT / ".env"
DB_FILE = DB_DIR / "tradebot.sqlite3"
LOCK_FILE = DB_DIR / "tradebot.lock"
CONTROL_TOKEN_FILE = DB_DIR / "control.token"


def ensure_dirs() -> None:
    for d in (LOG_DIR, DB_DIR, BACKUP_DIR, EXPORT_DIR, EXPORT_DIR / "reports"):
        d.mkdir(parents=True, exist_ok=True)
