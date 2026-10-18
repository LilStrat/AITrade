"""Backup and restore of the database and your settings.

Backups never include the .env file (secrets). Restoring first makes a
backup of the current state, so a restore can itself be undone.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from . import paths
from .db import Database

INCLUDED_CONFIG = ("settings.yaml", "universe.yaml")


def create_backup(db: Database | None = None, label: str = "backup") -> Path:
    paths.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = paths.BACKUP_DIR / f"{label}-{stamp}.zip"
    with tempfile.TemporaryDirectory() as tmp:
        db_copy = Path(tmp) / "tradebot.sqlite3"
        own = db is None
        db = db or Database(paths.DB_FILE)
        try:
            db.backup_to(db_copy)
        finally:
            if own:
                db.close()
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.write(db_copy, "database/tradebot.sqlite3")
            for name in INCLUDED_CONFIG:
                f = paths.CONFIG_DIR / name
                if f.exists():
                    z.write(f, f"config/{name}")
            z.writestr("BACKUP_INFO.txt", f"Trading bot backup created {datetime.now().isoformat()}\n"
                                          "Contains the database and your settings. Secrets (.env) are NOT included.\n")
    return target


def restore_backup(archive: Path, confirmation: str) -> Path:
    """Restore a backup. The app must be stopped (caller holds the instance lock)."""
    if confirmation != "RESTORE":
        raise ValueError("Type RESTORE to confirm")
    archive = Path(archive)
    with zipfile.ZipFile(archive) as z:
        names = set(z.namelist())
        if "database/tradebot.sqlite3" not in names:
            raise ValueError("This file is not a trading bot backup")
        for n in names:  # refuse path traversal
            if n.startswith("/") or ".." in Path(n).parts:
                raise ValueError("Unsafe file path inside backup")
        safety = create_backup(label="before-restore") if paths.DB_FILE.exists() else None
        with tempfile.TemporaryDirectory() as tmp:
            z.extractall(tmp)
            src = Path(tmp) / "database" / "tradebot.sqlite3"
            Database(src).close()  # validates it opens and migrates
            for suffix in ("-wal", "-shm"):
                p = Path(str(paths.DB_FILE) + suffix)
                if p.exists():
                    p.unlink()
            paths.DB_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, paths.DB_FILE)
            for name in INCLUDED_CONFIG:
                f = Path(tmp) / "config" / name
                if f.exists():
                    tmpf = paths.CONFIG_DIR / (name + ".restore")
                    shutil.copy2(f, tmpf)
                    os.replace(tmpf, paths.CONFIG_DIR / name)
    return safety or archive
