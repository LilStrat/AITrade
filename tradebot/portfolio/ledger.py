"""Portfolio accounting in GBP, derived entirely from the append-only ledger.

Positions, cash and P&L are recomputed by replaying transactions, so the
numbers are always auditable. Each economic event has a unique ``ref`` and
is rejected if recorded twice.

Cost basis uses the average-cost method (each position's GBP basis includes
buying costs). This is the basis used for P&L in the dashboard. The UK tax
estimate in ``tax.py`` applies HMRC's share matching rules separately.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone

from ..db import Database


class DuplicateTransaction(Exception):
    pass


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Position:
    symbol: str
    quantity: float = 0.0
    avg_price: float = 0.0          # local currency per share, excluding costs
    cost_basis_gbp: float = 0.0     # GBP, including buying costs
    currency: str = "GBP"
    realised_pnl_gbp: float = 0.0
    dividends_gbp: float = 0.0
    fees_gbp: float = 0.0
    opened: str | None = None


@dataclass
class AccountState:
    account_id: int
    cash_gbp: float
    reserved_gbp: float
    positions: dict[str, Position]
    closed: dict[str, Position] = field(default_factory=dict)
    realised_pnl_gbp: float = 0.0
    dividends_gbp: float = 0.0
    fees_gbp: float = 0.0
    deposits_gbp: float = 0.0

    @property
    def available_cash_gbp(self) -> float:
        return self.cash_gbp - self.reserved_gbp


class Ledger:
    def __init__(self, db: Database):
        self.db = db

    # ----------------------------------------------------------- accounts
    def create_account(self, mode: str, name: str, starting_capital: float, cost_profile: str,
                       on: date | None = None) -> int:
        with self.db.tx() as c:
            cur = c.execute("INSERT INTO accounts(mode,name,starting_capital,cost_profile,created_at) VALUES (?,?,?,?,?)",
                            (mode, name, starting_capital, cost_profile, utcnow()))
            aid = int(cur.lastrowid)
            self._insert(c, aid, "DEPOSIT", (on or date.today()).isoformat(), None, 0, None, "GBP", 1.0,
                         round(starting_capital, 2), 0.0, f"deposit:initial:{aid}", "Starting capital (simulated)")
            c.execute("INSERT INTO risk_state(account_id, peak_equity, updated_at) VALUES (?,?,?)",
                      (aid, starting_capital, utcnow()))
        return aid

    def active_account(self, mode: str) -> dict | None:
        r = self.db.one("SELECT * FROM accounts WHERE mode=? AND status='active' ORDER BY id DESC LIMIT 1", (mode,))
        return dict(r) if r else None

    def archive_account(self, account_id: int) -> None:
        self.db.execute("UPDATE accounts SET status='archived', archived_at=? WHERE id=?", (utcnow(), account_id))

    # ----------------------------------------------------------- recording
    def _insert(self, c: sqlite3.Connection, account_id: int, ttype: str, trade_date: str, symbol: str | None,
                qty: float, price: float | None, currency: str | None, fx: float | None, cash_delta: float,
                fees: float, ref: str, note: str | None, corrects_id: int | None = None) -> int:
        try:
            cur = c.execute(
                """INSERT INTO transactions(account_id,ts,trade_date,type,symbol,quantity,price,currency,fx_rate,
                       cash_delta_gbp,fees_gbp,ref,note,corrects_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (account_id, utcnow(), trade_date, ttype, symbol, qty, price, currency, fx,
                 round(cash_delta, 2), round(fees, 2), ref, note, corrects_id))
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc):
                raise DuplicateTransaction(ref) from None
            raise
        return int(cur.lastrowid)

    def record_trade(self, c: sqlite3.Connection, account_id: int, side: str, trade_date: str, symbol: str,
                     qty: float, price: float, currency: str, fx: float, gross_gbp: float, fees_gbp: float,
                     ref: str, note: str) -> int:
        cash = -(gross_gbp + fees_gbp) if side == "BUY" else (gross_gbp - fees_gbp)
        return self._insert(c, account_id, side, trade_date, symbol, qty, price, currency, fx, cash, fees_gbp, ref, note)

    def record_dividend(self, account_id: int, trade_date: str, symbol: str, qty: float, per_share: float,
                        currency: str, fx: float, withholding_pct: float = 0.0) -> int | None:
        gross = qty * per_share * fx
        net = gross * (1 - withholding_pct / 100)
        note = f"Dividend {per_share:.4f} {currency}/share on {qty:g} shares (ex-date; simulated credit)"
        if withholding_pct:
            note += f", {withholding_pct:.0f}% withholding assumed"
        try:
            with self.db.tx() as c:
                return self._insert(c, account_id, "DIVIDEND", trade_date, symbol, qty, per_share, currency, fx,
                                    net, 0.0, f"div:{symbol}:{trade_date}", note)
        except DuplicateTransaction:
            return None

    def record_split(self, account_id: int, trade_date: str, symbol: str, ratio: float, qty_before: float) -> int | None:
        new_qty = qty_before * ratio
        try:
            with self.db.tx() as c:
                return self._insert(c, account_id, "SPLIT", trade_date, symbol, new_qty - qty_before, ratio, None, None,
                                    0.0, 0.0, f"split:{symbol}:{trade_date}",
                                    f"Split ratio {ratio:g}: {qty_before:g} -> {new_qty:g} shares")
        except DuplicateTransaction:
            return None

    def record_correction(self, account_id: int, cash_delta: float, note: str, corrects_id: int | None = None,
                          symbol: str | None = None, qty_delta: float = 0.0) -> int:
        with self.db.tx() as c:
            return self._insert(c, account_id, "CORRECTION", date.today().isoformat(), symbol, qty_delta, None, None,
                                None, cash_delta, 0.0, f"corr:{utcnow()}:{note[:40]}", note, corrects_id)

    # ----------------------------------------------------------- derived state
    def transactions(self, account_id: int) -> list[dict]:
        return [dict(r) for r in self.db.query(
            "SELECT * FROM transactions WHERE account_id=? ORDER BY trade_date, id", (account_id,))]

    def state(self, account_id: int, up_to: date | None = None) -> AccountState:
        """Replay the ledger. The full-history result is cached on the
        database object keyed by (account, latest transaction id, count), so
        any new transaction automatically invalidates it."""
        if up_to is None:
            last = self.db.one("SELECT COALESCE(MAX(id),0), COUNT(*) FROM transactions WHERE account_id=?", (account_id,))
            cache = self.db.__dict__.setdefault("_ledger_cache", {})  # lives and dies with this database
            key = (account_id, int(last[0]), int(last[1]))
            hit = cache.get(key)
            if hit is None:
                hit = self._replay(account_id, None)
                cache.clear()
                cache[key] = hit
            cash, pos, closed, realised, divs, fees, deposits = hit
            pos = {k: replace(v) for k, v in pos.items()}        # callers get their own copies
            closed = {k: replace(v) for k, v in closed.items()}
            r = self.db.one("""SELECT COALESCE(SUM(reserved_cash),0) FROM orders
                               WHERE account_id=? AND side='BUY' AND status IN ('PENDING','PARTIAL')""", (account_id,))
            return AccountState(account_id, round(cash, 2), round(float(r[0]), 2), pos, closed,
                                realised, divs, fees, deposits)
        cash, pos, closed, realised, divs, fees, deposits = self._replay(account_id, up_to)
        return AccountState(account_id, round(cash, 2), 0.0, pos, closed, realised, divs, fees, deposits)

    def _replay(self, account_id: int, up_to: date | None):
        cash = deposits = realised = divs = fees = 0.0
        pos: dict[str, Position] = {}
        closed: dict[str, Position] = {}
        for t in self.transactions(account_id):
            if up_to is not None and date.fromisoformat(t["trade_date"]) > up_to:
                continue
            cash += t["cash_delta_gbp"]
            sym, typ, q = t["symbol"], t["type"], t["quantity"]
            if typ == "DEPOSIT":
                deposits += t["cash_delta_gbp"]
                continue
            if sym is None:
                continue
            p = pos.get(sym) or closed.pop(sym, None) or Position(sym, currency=t["currency"] or "GBP")
            if typ == "BUY":
                gross = -t["cash_delta_gbp"] - t["fees_gbp"]
                if p.quantity <= 0:
                    p.opened = t["trade_date"]
                p.avg_price = (p.avg_price * p.quantity + t["price"] * q) / (p.quantity + q)
                p.quantity += q
                p.cost_basis_gbp += gross + t["fees_gbp"]
                p.fees_gbp += t["fees_gbp"]
                p.currency = t["currency"] or p.currency
            elif typ == "SELL":
                if q > p.quantity + 1e-9:
                    raise ValueError(f"ledger sells more {sym} than held - accounting discrepancy")
                proceeds = t["cash_delta_gbp"]
                basis_out = p.cost_basis_gbp * (q / p.quantity) if p.quantity else 0.0
                pnl = proceeds - basis_out
                p.realised_pnl_gbp += pnl
                realised += pnl
                p.cost_basis_gbp -= basis_out
                p.quantity -= q
                p.fees_gbp += t["fees_gbp"]
                if abs(p.quantity) < 1e-9:
                    p.quantity, p.cost_basis_gbp, p.avg_price = 0.0, 0.0, 0.0
            elif typ == "SPLIT":
                ratio = t["price"]
                p.quantity += q
                if ratio:
                    p.avg_price /= ratio
            elif typ == "DIVIDEND":
                p.dividends_gbp += t["cash_delta_gbp"]
                divs += t["cash_delta_gbp"]
            elif typ == "CORRECTION":
                p.quantity += q
            fees += t["fees_gbp"]
            if p.quantity > 1e-9:
                pos[sym] = p
            else:
                pos.pop(sym, None)
                closed[sym] = p
        return cash, pos, closed, realised, divs, fees, deposits
