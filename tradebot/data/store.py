"""Market data service: download, validate, store and serve daily bars."""

from __future__ import annotations

import logging
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from ..db import Database
from . import calendar as cal
from .providers.base import DataProviderError, FetchResult, MarketDataProvider
from .validation import check_overlap, validate_bars

log = logging.getLogger(__name__)

FX_SYMBOLS = {"USD": "GBPUSD=X", "EUR": "GBPEUR=X"}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_provider(name: str) -> MarketDataProvider:
    if name == "yahoo":
        from .providers.yahoo import YahooProvider
        return YahooProvider()
    if name == "stooq":
        from .providers.stooq import StooqProvider
        return StooqProvider()
    if name == "eodhd":
        from .providers.eodhd import EodhdProvider
        return EodhdProvider()
    if name == "synthetic":
        from .providers.synthetic import SyntheticProvider
        return SyntheticProvider()
    raise ValueError(f"unknown data provider {name!r}")


@dataclass
class UpdateResult:
    symbol: str
    ok: bool
    source: str | None
    new_bars: int
    message: str
    restated: bool = False
    new_splits: list[tuple[date, float]] | None = None


class MarketData:
    def __init__(self, db: Database, settings: dict, primary: MarketDataProvider | None = None,
                 fallback: MarketDataProvider | None = None, sleep=_time.sleep):
        self.db = db
        self.settings = settings
        ds = settings["data"]
        self.primary = primary or build_provider(ds["provider"])
        fb = ds.get("fallback_provider")
        self.fallback = fallback if fallback is not None else (
            build_provider(fb) if fb and fb != ds["provider"] and ds["provider"] != "synthetic" else None)
        self._sleep = sleep
        self._cache: dict[str, pd.DataFrame] = {}

    # ------------------------------------------------------------------ setup
    def sync_instruments(self, universe: list[dict]) -> None:
        with self.db.tx() as c:
            for ins in universe:
                c.execute(
                    """INSERT INTO instruments(symbol,name,type,sector,tier,currency,exchange,enabled)
                       VALUES (?,?,?,?,?,?,?,?)
                       ON CONFLICT(symbol) DO UPDATE SET name=excluded.name, type=excluded.type,
                         sector=excluded.sector, tier=excluded.tier, exchange=excluded.exchange,
                         enabled=excluded.enabled,
                         currency=CASE WHEN excluded.currency!='AUTO' THEN excluded.currency ELSE instruments.currency END""",
                    (ins["symbol"], ins["name"], ins["type"], ins["sector"], ins["tier"],
                     ins["currency"], ins["exchange"], int(ins["enabled"])))
            # instruments removed from the universe file are disabled, not deleted
            symbols = [i["symbol"] for i in universe]
            if symbols:
                q = ",".join("?" * len(symbols))
                c.execute(f"UPDATE instruments SET enabled=0 WHERE symbol NOT IN ({q})", symbols)

    def instruments(self, enabled_only: bool = True) -> list[dict]:
        sql = "SELECT * FROM instruments" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY symbol"
        return [dict(r) for r in self.db.query(sql)]

    def instrument(self, symbol: str) -> dict | None:
        r = self.db.one("SELECT * FROM instruments WHERE symbol=?", (symbol,))
        return dict(r) if r else None

    # ---------------------------------------------------------------- updates
    def _fetch_with_retry(self, provider: MarketDataProvider, symbol: str, start: date, end: date,
                          hint: str | None) -> FetchResult:
        retries = int(self.settings["data"].get("max_retries", 3))
        delay = float(self.settings["data"].get("request_delay_seconds", 1.0))
        last: Exception | None = None
        for attempt in range(retries):
            try:
                return provider.fetch_daily(symbol, start, end, hint)
            except DataProviderError as exc:
                last = exc
                if attempt < retries - 1:
                    self._sleep(delay * (2 ** attempt))
        raise DataProviderError(str(last))

    def stored_bars(self, symbol: str) -> pd.DataFrame:
        rows = self.db.query("SELECT date,open,high,low,close,adj_close,volume,source FROM bars WHERE symbol=? ORDER BY date",
                             (symbol,))
        df = pd.DataFrame([dict(r) for r in rows],
                          columns=["date", "open", "high", "low", "close", "adj_close", "volume", "source"])
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"]).dt.date
        return df

    def update_symbol(self, symbol: str, now: datetime | None = None, full: bool = False) -> UpdateResult:
        now = now or cal.now_london()
        last_complete = cal.last_completed_session(now)
        ins = self.instrument(symbol) or {"currency": "AUTO", "exchange": "FX" if symbol.endswith("=X") else "LSE"}
        stored = self.stored_bars(symbol)
        years = int(self.settings["data"]["history_years"])
        if stored.empty or full:
            start = last_complete - timedelta(days=int(365.25 * years))
        else:
            start = stored["date"].iloc[-1] - timedelta(days=14)
        self.db.execute("INSERT INTO data_status(symbol,last_attempt_at) VALUES (?,?) "
                        "ON CONFLICT(symbol) DO UPDATE SET last_attempt_at=excluded.last_attempt_at",
                        (symbol, utcnow()))

        result: FetchResult | None = None
        errors = []
        for prov in [p for p in (self.primary, self.fallback) if p is not None]:
            hint = ins.get("currency")
            try:
                result = self._fetch_with_retry(prov, symbol, start, last_complete, hint)
                break
            except DataProviderError as exc:
                errors.append(f"{prov.name}: {exc}")
        if result is None:
            msg = "; ".join(errors)
            self._status_error(symbol, msg)
            return UpdateResult(symbol, False, None, 0, msg)

        exchange = ins.get("exchange", "LSE")
        rep = validate_bars(result.bars, suspicious_move_pct=float(self.settings["data"]["suspicious_move_pct"]),
                            last_complete=last_complete if exchange != "US" else last_complete + timedelta(days=1),
                            exchange=exchange)
        for sev, msg in rep.issues:
            self.quality_event(symbol, sev, f"[{result.source}] {msg}")
        for msg in result.warnings:
            self.quality_event(symbol, "warning", f"[{result.source}] {msg}")
        if rep.clean.empty:
            self._status_error(symbol, "no valid bars after validation")
            return UpdateResult(symbol, False, result.source, 0, "no valid bars after validation")

        restated = False
        if not stored.empty and not full:
            ok, why = check_overlap(stored, rep.clean)
            if not ok:
                if result.source == (self.primary.name if self.primary else None):
                    self.quality_event(symbol, "warning", f"history restated by {result.source} ({why}); reloading full history")
                    res = self.update_symbol(symbol, now, full=True)
                    res.restated = True
                    return res
                msg = f"fallback {result.source} disagrees with stored data ({why}); update rejected"
                self.quality_event(symbol, "error", msg)
                self._status_error(symbol, msg)
                return UpdateResult(symbol, False, result.source, 0, msg)

        new_splits: list[tuple[date, float]] = []
        fetched = utcnow()
        with self.db.tx() as c:
            if full:
                c.execute("DELETE FROM bars WHERE symbol=?", (symbol,))
                restated = not stored.empty
            existing = set() if full else {str(d) for d in stored["date"]}
            new_count = 0
            for r in rep.clean.itertuples(index=False):
                ds = str(r.date)
                if ds not in existing:
                    new_count += 1
                c.execute("""INSERT INTO bars(symbol,date,open,high,low,close,adj_close,volume,currency,source,fetched_at)
                             VALUES (?,?,?,?,?,?,?,?,?,?,?)
                             ON CONFLICT(symbol,date) DO UPDATE SET open=excluded.open, high=excluded.high,
                               low=excluded.low, close=excluded.close, adj_close=excluded.adj_close,
                               volume=excluded.volume, currency=excluded.currency, source=excluded.source,
                               fetched_at=excluded.fetched_at""",
                          (symbol, ds, float(r.open), float(r.high), float(r.low), float(r.close),
                           float(r.adj_close), float(r.volume), result.currency, result.source, fetched))
            for a in result.actions:
                cur = c.execute("""INSERT OR IGNORE INTO corporate_actions(symbol,date,type,value,currency,source,fetched_at)
                                   VALUES (?,?,?,?,?,?,?)""",
                                (symbol, a.date.isoformat(), a.type, a.value, a.currency, result.source, fetched))
                if cur.rowcount and a.type == "split":
                    new_splits.append((a.date, a.value))
            if result.raw_currency:
                prev = ins.get("currency")
                if prev not in (None, "AUTO", result.raw_currency):
                    self.quality_event(symbol, "warning",
                                       f"{result.source} reports currency {result.raw_currency}, previously {prev}")
                c.execute("UPDATE instruments SET currency=? WHERE symbol=?", (result.raw_currency, symbol))
            last_bar = str(rep.clean["date"].iloc[-1])
            c.execute("""INSERT INTO data_status(symbol,source,last_bar_date,last_success_at,last_error,quality_flag,delay_note)
                         VALUES (?,?,?,?,NULL,?,?)
                         ON CONFLICT(symbol) DO UPDATE SET source=excluded.source, last_bar_date=excluded.last_bar_date,
                           last_success_at=excluded.last_success_at, last_error=NULL,
                           quality_flag=excluded.quality_flag, delay_note=excluded.delay_note""",
                      (symbol, result.source, last_bar, fetched, "suspicious" if rep.suspicious else "ok",
                       result.delay_note))
        self._cache.pop(symbol, None)
        return UpdateResult(symbol, True, result.source, new_count,
                            f"{new_count} new bar(s); last bar {last_bar}", restated, new_splits)

    def update_all(self, symbols: list[str] | None = None, now: datetime | None = None,
                   progress=None) -> list[UpdateResult]:
        symbols = symbols or [i["symbol"] for i in self.instruments()]
        ccys = {i["currency"] for i in self.instruments() if i["symbol"] in symbols}
        fx = [FX_SYMBOLS[c] for c in ccys if c in FX_SYMBOLS]
        out = []
        delay = float(self.settings["data"].get("request_delay_seconds", 1.0))
        for n, s in enumerate(symbols + fx):
            try:
                res = self.update_symbol(s, now)
            except Exception as exc:  # never let one symbol stop the rest
                log.exception("update failed for %s", s)
                self._status_error(s, f"unexpected error: {type(exc).__name__}")
                res = UpdateResult(s, False, None, 0, f"unexpected error: {exc}")
            out.append(res)
            if progress:
                progress(n + 1, len(symbols) + len(fx), res)
            if n < len(symbols) + len(fx) - 1 and self.primary.name != "synthetic":
                self._sleep(delay)
        return out

    # ---------------------------------------------------------------- queries
    def bars(self, symbol: str, end: date | None = None) -> pd.DataFrame:
        """Bars indexed by date (ascending), optionally truncated at ``end``."""
        if symbol not in self._cache:
            df = self.stored_bars(symbol)
            df.index = pd.Index(df["date"], name="date")
            self._cache[symbol] = df.drop(columns=["date"])
        df = self._cache[symbol]
        if end is not None:
            df = df[df.index <= end]
        return df

    def price_currency(self, symbol: str) -> str:
        """Major currency that stored prices for ``symbol`` are expressed in."""
        r = self.db.one("SELECT currency FROM bars WHERE symbol=? ORDER BY date DESC LIMIT 1", (symbol,))
        if r and r["currency"]:
            return r["currency"]
        ins = self.instrument(symbol)
        raw = (ins or {}).get("currency", "AUTO")
        if raw in ("GBX", "GBP", "AUTO") and symbol.endswith(".L"):
            return "GBP"
        return "USD" if raw in ("AUTO", "USD") else raw

    def actions(self, symbol: str, start: date | None = None, end: date | None = None) -> list[dict]:
        rows = self.db.query("SELECT * FROM corporate_actions WHERE symbol=? ORDER BY date", (symbol,))
        out = []
        for r in rows:
            d = date.fromisoformat(r["date"])
            if (start is None or d >= start) and (end is None or d <= end):
                out.append({**dict(r), "date": d})
        return out

    def fx_to_gbp(self, currency: str, on: date) -> float | None:
        """GBP value of one unit of ``currency`` on (or just before) ``on``."""
        if currency == "GBP":
            return 1.0
        sym = FX_SYMBOLS.get(currency)
        if not sym:
            return None
        df = self.bars(sym, end=on)
        if df.empty:
            return None
        last_date = df.index[-1]
        if (on - last_date).days > 7:
            return None  # FX rate too old to use
        return 1.0 / float(df["close"].iloc[-1])

    def status(self, symbol: str, now: datetime | None = None) -> dict:
        r = self.db.one("SELECT * FROM data_status WHERE symbol=?", (symbol,))
        expected = cal.last_completed_session(now)
        info = dict(r) if r else {"symbol": symbol, "last_bar_date": None, "source": None}
        last = info.get("last_bar_date")
        if not last:
            info.update(fresh=False, sessions_behind=None, expected_date=str(expected), state="no data")
            return info
        last_d = date.fromisoformat(last)
        behind = len(cal.trading_days_between(last_d + timedelta(days=1), expected))
        allowed = int(self.settings["data"]["stale_after_sessions"])
        suspicious = info.get("quality_flag") == "suspicious"
        fresh = behind <= allowed and not suspicious
        info.update(fresh=fresh, sessions_behind=behind, expected_date=str(expected),
                    state="suspicious move - review" if suspicious else ("fresh" if fresh else "stale"))
        return info

    def quality_event(self, symbol: str | None, severity: str, message: str) -> None:
        self.db.execute("INSERT INTO data_quality_events(ts,symbol,severity,message) VALUES (?,?,?,?)",
                        (utcnow(), symbol, severity, message[:1000]))
        if severity in ("warning", "error"):
            log.warning("data quality %s: %s", symbol, message)

    def _status_error(self, symbol: str, msg: str) -> None:
        self.db.execute("INSERT INTO data_status(symbol,last_error,last_attempt_at) VALUES (?,?,?) "
                        "ON CONFLICT(symbol) DO UPDATE SET last_error=excluded.last_error, last_attempt_at=excluded.last_attempt_at",
                        (symbol, msg[:1000], utcnow()))
        self.quality_event(symbol, "error", msg)

    def clear_suspicious(self, symbol: str) -> None:
        """User has reviewed a flagged move and confirms the data is correct."""
        self.db.execute("UPDATE data_status SET quality_flag='ok (reviewed)' WHERE symbol=?", (symbol,))
        self.quality_event(symbol, "info", "suspicious-move flag cleared by user review")
