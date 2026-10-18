"""SQLite persistence with versioned migrations.

Money-relevant history (transactions, fills, order events) is append-only:
database triggers refuse UPDATE and DELETE on those tables, so corrections
must be recorded as new, explicit CORRECTION entries.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from . import paths

MIGRATIONS: list[str] = [
    # ---- v1 -----------------------------------------------------------------
    """
    CREATE TABLE instruments (
        symbol TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        type TEXT NOT NULL CHECK (type IN ('stock','etf')),
        sector TEXT NOT NULL DEFAULT 'Unknown',
        tier TEXT NOT NULL DEFAULT 'default',
        currency TEXT NOT NULL DEFAULT 'AUTO',
        exchange TEXT NOT NULL DEFAULT 'LSE',
        enabled INTEGER NOT NULL DEFAULT 1
    );

    -- Daily bars. Prices are stored in the MAJOR currency unit (GBP, USD),
    -- split-adjusted but NOT dividend-adjusted. adj_close is the provider's
    -- dividend+split adjusted close, kept for reference only.
    CREATE TABLE bars (
        symbol TEXT NOT NULL,
        date TEXT NOT NULL,
        open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
        adj_close REAL,
        volume REAL NOT NULL,
        currency TEXT NOT NULL,
        source TEXT NOT NULL,
        fetched_at TEXT NOT NULL,
        PRIMARY KEY (symbol, date)
    );

    CREATE TABLE corporate_actions (
        symbol TEXT NOT NULL,
        date TEXT NOT NULL,
        type TEXT NOT NULL CHECK (type IN ('dividend','split')),
        value REAL NOT NULL,
        currency TEXT,
        source TEXT NOT NULL,
        fetched_at TEXT NOT NULL,
        PRIMARY KEY (symbol, date, type)
    );

    CREATE TABLE data_status (
        symbol TEXT PRIMARY KEY,
        source TEXT,
        last_bar_date TEXT,
        last_success_at TEXT,
        last_attempt_at TEXT,
        last_error TEXT,
        quality_flag TEXT,
        delay_note TEXT
    );

    CREATE TABLE data_quality_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        symbol TEXT,
        severity TEXT NOT NULL,
        message TEXT NOT NULL
    );

    CREATE TABLE accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mode TEXT NOT NULL,
        name TEXT NOT NULL,
        starting_capital REAL NOT NULL,
        cost_profile TEXT NOT NULL,
        created_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
        archived_at TEXT
    );

    CREATE TABLE orders (
        id TEXT PRIMARY KEY,
        account_id INTEGER NOT NULL REFERENCES accounts(id),
        client_order_id TEXT NOT NULL UNIQUE,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
        order_type TEXT NOT NULL CHECK (order_type IN ('MARKET','STOP','LIMIT')),
        quantity REAL NOT NULL CHECK (quantity > 0),
        limit_price REAL,
        stop_price REAL,
        status TEXT NOT NULL,
        filled_quantity REAL NOT NULL DEFAULT 0,
        avg_fill_price REAL,
        est_price REAL,
        reserved_cash REAL NOT NULL DEFAULT 0,
        stop_distance REAL,
        strategy TEXT,
        purpose TEXT NOT NULL,
        reason TEXT,
        created_for_date TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX ix_orders_status ON orders(account_id, status);

    CREATE TABLE order_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id TEXT NOT NULL REFERENCES orders(id),
        ts TEXT NOT NULL,
        status TEXT NOT NULL,
        message TEXT
    );

    CREATE TABLE fills (
        id TEXT PRIMARY KEY,
        order_id TEXT NOT NULL REFERENCES orders(id),
        account_id INTEGER NOT NULL,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        quantity REAL NOT NULL,
        price REAL NOT NULL,
        currency TEXT NOT NULL,
        fx_rate REAL NOT NULL,
        gross_gbp REAL NOT NULL,
        commission_gbp REAL NOT NULL,
        stamp_duty_gbp REAL NOT NULL,
        other_fees_gbp REAL NOT NULL,
        fx_cost_gbp REAL NOT NULL,
        spread_slippage_gbp REAL NOT NULL,
        trade_date TEXT NOT NULL,
        ts TEXT NOT NULL,
        execution_note TEXT
    );

    -- The ledger. Cash balance = SUM(cash_delta_gbp). ref is a unique
    -- idempotency key so the same economic event can never be booked twice.
    CREATE TABLE transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL REFERENCES accounts(id),
        ts TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        type TEXT NOT NULL CHECK (type IN ('DEPOSIT','BUY','SELL','DIVIDEND','SPLIT','FEE','CORRECTION')),
        symbol TEXT,
        quantity REAL NOT NULL DEFAULT 0,
        price REAL,
        currency TEXT,
        fx_rate REAL,
        cash_delta_gbp REAL NOT NULL,
        fees_gbp REAL NOT NULL DEFAULT 0,
        ref TEXT NOT NULL,
        note TEXT,
        corrects_id INTEGER REFERENCES transactions(id),
        UNIQUE (account_id, ref)
    );

    CREATE TRIGGER transactions_no_update BEFORE UPDATE ON transactions
    BEGIN SELECT RAISE(ABORT, 'transactions are append-only; record a CORRECTION instead'); END;
    CREATE TRIGGER transactions_no_delete BEFORE DELETE ON transactions
    BEGIN SELECT RAISE(ABORT, 'transactions are append-only; record a CORRECTION instead'); END;
    CREATE TRIGGER fills_no_update BEFORE UPDATE ON fills
    BEGIN SELECT RAISE(ABORT, 'fills are append-only'); END;
    CREATE TRIGGER fills_no_delete BEFORE DELETE ON fills
    BEGIN SELECT RAISE(ABORT, 'fills are append-only'); END;
    CREATE TRIGGER order_events_no_update BEFORE UPDATE ON order_events
    BEGIN SELECT RAISE(ABORT, 'order events are append-only'); END;
    CREATE TRIGGER order_events_no_delete BEFORE DELETE ON order_events
    BEGIN SELECT RAISE(ABORT, 'order events are append-only'); END;

    -- Working state for open positions (stop level, strategy, entry date).
    CREATE TABLE position_meta (
        account_id INTEGER NOT NULL,
        symbol TEXT NOT NULL,
        strategy TEXT,
        entry_date TEXT,
        initial_stop REAL,
        current_stop REAL,
        target REAL,
        highest_close REAL,
        bars_held INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (account_id, symbol)
    );

    CREATE TABLE equity_snapshots (
        account_id INTEGER NOT NULL,
        date TEXT NOT NULL,
        cash_gbp REAL NOT NULL,
        positions_gbp REAL NOT NULL,
        equity_gbp REAL NOT NULL,
        realised_pnl_gbp REAL NOT NULL,
        unrealised_pnl_gbp REAL NOT NULL,
        PRIMARY KEY (account_id, date)
    );

    CREATE TABLE risk_state (
        account_id INTEGER PRIMARY KEY,
        day TEXT,
        day_start_equity REAL,
        peak_equity REAL,
        paused_reason TEXT,
        daily_pause_day TEXT,
        kill_switch INTEGER NOT NULL DEFAULT 0,
        consecutive_failures INTEGER NOT NULL DEFAULT 0,
        account_uncertain INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT
    );

    CREATE TABLE signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER,
        as_of_date TEXT NOT NULL,
        symbol TEXT NOT NULL,
        strategy TEXT NOT NULL,
        action TEXT NOT NULL,
        entry_est REAL,
        stop REAL,
        target REAL,
        rank_score REAL,
        reasons TEXT,
        risks TEXT,
        risk_decision TEXT,
        status TEXT NOT NULL,
        order_id TEXT,
        created_at TEXT NOT NULL
    );
    CREATE INDEX ix_signals_date ON signals(as_of_date);

    CREATE TABLE alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        level TEXT NOT NULL,
        category TEXT NOT NULL,
        message TEXT NOT NULL,
        acknowledged INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL,
        kind TEXT NOT NULL,
        run_date TEXT NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        status TEXT NOT NULL,
        message TEXT,
        UNIQUE (account_id, kind, run_date)
    );

    CREATE TABLE ai_notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        kind TEXT NOT NULL,
        subject TEXT,
        facts TEXT NOT NULL,
        output TEXT,
        model TEXT,
        status TEXT NOT NULL
    );

    CREATE TABLE backtest_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        status TEXT NOT NULL,
        label TEXT,
        request TEXT NOT NULL,
        result TEXT,
        error TEXT
    );

    CREATE TABLE settings_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        path TEXT NOT NULL,
        old_value TEXT,
        new_value TEXT
    );
    """,
]


class Database:
    """Thin wrapper around sqlite3 with a per-thread connection."""

    def __init__(self, path: Path | str | None = None):
        self.path = str(path or paths.DB_FILE)
        self._local = threading.local()
        self._write_lock = threading.RLock()
        self._memory_conn: sqlite3.Connection | None = None
        if self.path == ":memory:":
            self._memory_conn = self._connect()
        self.migrate()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=self.path != ":memory:",
                               isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = FULL")
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        if self._memory_conn is not None:
            return self._memory_conn
        c = getattr(self._local, "conn", None)
        if c is None:
            c = self._connect()
            self._local.conn = c
        return c

    def migrate(self) -> None:
        c = self.conn
        c.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        row = c.execute("SELECT MAX(version) FROM schema_version").fetchone()
        current = row[0] or 0
        for i, script in enumerate(MIGRATIONS, start=1):
            if i <= current:
                continue
            with self._write_lock:
                c.execute("BEGIN IMMEDIATE")
                try:
                    for stmt in _split_sql(script):
                        c.execute(stmt)
                    c.execute("INSERT INTO schema_version(version) VALUES (?)", (i,))
                    c.execute("COMMIT")
                except Exception:
                    c.execute("ROLLBACK")
                    raise

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """An atomic write transaction: everything inside commits or nothing does."""
        with self._write_lock:
            c = self.conn
            if c.in_transaction:  # nested - join the outer transaction
                yield c
                return
            c.execute("BEGIN IMMEDIATE")
            try:
                yield c
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._write_lock:
            return self.conn.execute(sql, params)

    def backup_to(self, target: Path) -> None:
        """Consistent online copy (safe while the app is running)."""
        dest = sqlite3.connect(str(target))
        try:
            self.conn.backup(dest)
        finally:
            dest.close()

    def close(self) -> None:
        c = getattr(self._local, "conn", None)
        if c is not None:
            c.close()
            self._local.conn = None
        if self._memory_conn is not None:
            self._memory_conn.close()
            self._memory_conn = None


def _split_sql(script: str) -> list[str]:
    """Split a migration into statements, keeping trigger bodies intact."""
    stmts, buf, depth = [], [], 0
    for line in script.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") or not stripped:
            continue
        buf.append(line)
        upper = stripped.upper()
        if upper.startswith("CREATE TRIGGER"):
            depth += 1
        if depth and upper.endswith("END;"):
            depth -= 1
        if stripped.endswith(";") and depth == 0:
            stmts.append("\n".join(buf).rstrip().rstrip(";"))
            buf = []
    if buf and "".join(buf).strip():
        stmts.append("\n".join(buf))
    return stmts
