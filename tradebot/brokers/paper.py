"""Local paper broker: a persistent simulated account on this computer.

No network, no real broker, no real money. Orders are filled by the shared
execution model in ``tradebot.execution`` against daily bars.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone

from ..costs import CostModel
from ..db import Database
from ..execution import simulate_market, simulate_sell_limit, simulate_sell_stop
from ..portfolio.ledger import Ledger
from .base import OPEN_STATUSES, BrokerAdapter, BrokerError, OrderRequest

log = logging.getLogger(__name__)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class LocalPaperBroker(BrokerAdapter):
    name = "local_paper"
    is_live = False

    def __init__(self, db: Database, account_id: int, cost_model: CostModel, settings: dict):
        self.db = db
        self.account_id = account_id
        self.ledger = Ledger(db)
        self.costs = cost_model
        self.max_bar_pct = float(settings["execution"]["max_pct_of_bar_volume"])

    # ---------------------------------------------------------------- orders
    def submit_order(self, req: OrderRequest) -> dict:
        if req.side not in ("BUY", "SELL") or req.order_type not in ("MARKET", "STOP", "LIMIT"):
            raise BrokerError("invalid order")
        if req.quantity <= 0:
            raise BrokerError("quantity must be positive")
        if req.side == "SELL":
            held = self.ledger.state(self.account_id).positions.get(req.symbol)
            if held is None or req.quantity > held.quantity + 1e-9:
                raise BrokerError(f"cannot sell {req.quantity} {req.symbol}: not held (no short selling)")
        existing = self.find_by_client_id(req.client_order_id)
        if existing:
            return existing  # idempotent: never create a duplicate
        oid = uuid.uuid4().hex
        now = utcnow()
        with self.db.tx() as c:
            c.execute("""INSERT INTO orders(id,account_id,client_order_id,symbol,side,order_type,quantity,limit_price,
                            stop_price,status,est_price,reserved_cash,stop_distance,strategy,purpose,reason,
                            created_for_date,created_at,updated_at)
                         VALUES (?,?,?,?,?,?,?,?,?,'PENDING',?,?,?,?,?,?,?,?,?)""",
                      (oid, self.account_id, req.client_order_id, req.symbol, req.side, req.order_type, req.quantity,
                       req.limit_price, req.stop_price, req.est_price, round(req.reserved_cash, 2), req.stop_distance,
                       req.strategy, req.purpose, req.reason, req.created_for_date, now, now))
            c.execute("INSERT INTO order_events(order_id,ts,status,message) VALUES (?,?,?,?)",
                      (oid, now, "PENDING", f"{req.purpose} order accepted by local paper broker (simulated)"))
        return self.get_order(oid)

    def _set_status(self, c, order_id: str, status: str, message: str, **fields) -> None:
        sets = ", ".join([f"{k}=?" for k in fields] + ["status=?", "updated_at=?"])
        c.execute(f"UPDATE orders SET {sets} WHERE id=?", (*fields.values(), status, utcnow(), order_id))
        c.execute("INSERT INTO order_events(order_id,ts,status,message) VALUES (?,?,?,?)",
                  (order_id, utcnow(), status, message))

    def cancel_order(self, order_id: str, reason: str) -> None:
        o = self.get_order(order_id)
        if o is None or o["status"] not in OPEN_STATUSES:
            return
        with self.db.tx() as c:
            self._set_status(c, order_id, "CANCELLED", reason)

    def get_order(self, order_id: str) -> dict | None:
        r = self.db.one("SELECT * FROM orders WHERE id=?", (order_id,))
        return dict(r) if r else None

    def find_by_client_id(self, client_order_id: str) -> dict | None:
        r = self.db.one("SELECT * FROM orders WHERE client_order_id=?", (client_order_id,))
        return dict(r) if r else None

    def open_orders(self, symbol: str | None = None) -> list[dict]:
        sql = "SELECT * FROM orders WHERE account_id=? AND status IN ('PENDING','PARTIAL')"
        params: tuple = (self.account_id,)
        if symbol:
            sql += " AND symbol=?"
            params += (symbol,)
        return [dict(r) for r in self.db.query(sql + " ORDER BY rowid", params)]  # insertion order: deterministic

    def account_state(self):
        return self.ledger.state(self.account_id)

    def is_connected(self) -> bool:
        return True

    # ---------------------------------------------------------- simulation
    def _book_fill(self, order: dict, qty: float, price: float, ref_price: float, note: str, day: date,
                   currency: str, fx: float, instrument: dict) -> dict:
        n = self.db.one("SELECT COUNT(*) FROM fills WHERE order_id=?", (order["id"],))[0]
        fill_id = f"{order['id']}:{n + 1}"
        gross = qty * price * fx
        fees = self.costs.fees(order["side"], qty, price, currency, fx, instrument)
        impact = abs(price - ref_price) * qty * fx
        fill = {"id": fill_id, "order_id": order["id"], "symbol": order["symbol"], "side": order["side"],
                "quantity": qty, "price": price, "currency": currency, "fx_rate": fx, "gross_gbp": gross,
                "fees": fees, "impact_gbp": impact, "trade_date": day.isoformat(), "note": note,
                "purpose": order["purpose"], "strategy": order["strategy"]}
        with self.db.tx() as c:
            c.execute("""INSERT INTO fills(id,order_id,account_id,symbol,side,quantity,price,currency,fx_rate,gross_gbp,
                            commission_gbp,stamp_duty_gbp,other_fees_gbp,fx_cost_gbp,spread_slippage_gbp,trade_date,ts,
                            execution_note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (fill_id, order["id"], self.account_id, order["symbol"], order["side"], qty, price, currency, fx,
                       round(gross, 2), fees.commission_gbp, fees.stamp_duty_gbp, fees.other_fees_gbp,
                       fees.fx_cost_gbp, round(impact, 2), day.isoformat(), utcnow(), note))
            self.ledger.record_trade(c, self.account_id, order["side"], day.isoformat(), order["symbol"], qty, price,
                                     currency, fx, gross, fees.total_gbp, f"fill:{fill_id}",
                                     f"{order['purpose']}: {note}")
            filled = order["filled_quantity"] + qty
            avg = ((order["avg_fill_price"] or 0) * order["filled_quantity"] + price * qty) / filled
            status = "FILLED" if filled >= order["quantity"] - 1e-9 else "PARTIAL"
            self._set_status(c, order["id"], status, f"filled {qty:g} @ {price:.4f} {currency} ({note})",
                             filled_quantity=filled, avg_fill_price=avg)
        return fill

    def process_session(self, day: date, data, on_fill=None) -> list[dict]:
        """Simulate one trading session for all open orders created before ``day``.

        ``data`` is a DataView. ``on_fill(order, fill)`` is called after each
        fill is committed (the trading core uses it to place protective stops
        and cancel the other side of a stop/exit pair). Returns the fills.
        """
        fills: list[dict] = []
        eligible = lambda o: o["created_for_date"] < day.isoformat()  # noqa: E731 - never same-day fills

        # Phase 1: market orders at the open (sells first so cash is released).
        markets = [o for o in self.open_orders() if o["order_type"] == "MARKET" and eligible(o)]
        markets.sort(key=lambda o: 0 if o["side"] == "SELL" else 1)
        for o in markets:
            if self.get_order(o["id"])["status"] not in OPEN_STATUSES:
                continue
            for f in self._process_market(self.get_order(o["id"]), day, data):
                fills.append(f)
                if on_fill:
                    on_fill(self.get_order(f["order_id"]), f)

        # Phase 2: resting stops / limits (including stops placed on today's entries).
        for o in self.open_orders():
            if o["order_type"] in ("STOP", "LIMIT") and (eligible(o) or o["purpose"] == "stop"):
                if self.get_order(o["id"])["status"] not in OPEN_STATUSES:
                    continue  # cancelled by an earlier fill this session (OCO)
                for f in self._process_resting(self.get_order(o["id"]), day, data):
                    fills.append(f)
                    if on_fill:
                        on_fill(self.get_order(f["order_id"]), f)
        return fills

    def _position_qty(self, symbol: str) -> float:
        p = self.ledger.state(self.account_id).positions.get(symbol)
        return p.quantity if p else 0.0

    def _process_market(self, o: dict, day: date, data) -> list[dict]:
        bar = data.bar(o["symbol"], day)
        ins = data.instrument(o["symbol"])
        ccy = data.currency(o["symbol"])
        if bar is None or bar["volume"] <= 0:
            if o["purpose"] in ("exit", "flatten"):
                return []  # keep trying next session
            with self.db.tx() as c:
                self._set_status(c, o["id"], "EXPIRED", f"no trading in {o['symbol']} on {day} (no bar/zero volume)")
            return []
        fx = data.fx_to_gbp(ccy, day)
        if fx is None:
            with self.db.tx() as c:
                self._set_status(c, o["id"], "REJECTED", f"no {ccy}->GBP rate on {day}")
            return []
        remaining = o["quantity"] - o["filled_quantity"]
        if o["side"] == "SELL":
            remaining = min(remaining, self._position_qty(o["symbol"]))
            if remaining <= 0:
                with self.db.tx() as c:
                    self._set_status(c, o["id"], "CANCELLED", "position already closed")
                return []
        sim = simulate_market(o["side"], remaining, bar, self.costs.market_impact_bps(ins), self.max_bar_pct)
        if o["side"] == "BUY" and sim is not None:
            cash = self.account_state().cash_gbp
            est = sim.quantity * sim.price * fx
            fees = self.costs.fees("BUY", sim.quantity, sim.price, ccy, fx, ins).total_gbp
            if est + fees > cash + 1e-6:
                with self.db.tx() as c:
                    self._set_status(c, o["id"], "REJECTED",
                                     f"insufficient cash at open: needs £{est + fees:,.2f}, has £{cash:,.2f}")
                return []
        if sim is None:
            if o["purpose"] in ("exit", "flatten"):
                return []
            with self.db.tx() as c:
                self._set_status(c, o["id"], "EXPIRED", f"could not fill on {day} (volume cap)")
            return []
        fill = self._book_fill(o, sim.quantity, sim.price, sim.reference_price, sim.note, day, ccy, fx, ins)
        o2 = self.get_order(o["id"])
        if o2["status"] == "PARTIAL" and o["side"] == "BUY":
            with self.db.tx() as c:
                self._set_status(c, o["id"], "CANCELLED", "entry partly filled; remainder cancelled (volume cap)")
        return [fill]

    def _process_resting(self, o: dict, day: date, data) -> list[dict]:
        bar = data.bar(o["symbol"], day)
        if bar is None:
            return []
        ins, ccy = data.instrument(o["symbol"]), data.currency(o["symbol"])
        fx = data.fx_to_gbp(ccy, day)
        if fx is None:
            return []
        qty = min(o["quantity"] - o["filled_quantity"], self._position_qty(o["symbol"]))
        if qty <= 0:
            with self.db.tx() as c:
                self._set_status(c, o["id"], "CANCELLED", "position already closed")
            return []
        if o["order_type"] == "STOP":
            sim = simulate_sell_stop(qty, o["stop_price"], bar, self.costs.stop_impact_bps(ins), self.max_bar_pct)
        else:
            sim = simulate_sell_limit(qty, o["limit_price"], bar, self.costs.half_spread_bps(ins), self.max_bar_pct)
        if sim is None:
            return []
        return [self._book_fill(o, sim.quantity, sim.price, sim.reference_price, sim.note, day, ccy, fx, ins)]

    def cancel_open_sells(self, symbol: str, reason: str, except_id: str | None = None) -> None:
        for o in self.open_orders(symbol):
            if o["side"] == "SELL" and o["id"] != except_id:
                self.cancel_order(o["id"], reason)
