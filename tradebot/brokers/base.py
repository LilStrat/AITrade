"""Broker adapter interface (one internal order model for every broker).

Order life-cycle: PENDING -> (PARTIAL) -> FILLED | CANCELLED | EXPIRED | REJECTED

Rules every adapter must follow:
* ``submit_order`` is idempotent on ``client_order_id``: submitting the same
  id twice returns the existing order instead of creating a second one.
* After a timeout or connection error an adapter must look the order up by
  ``client_order_id`` before ever re-submitting it.
* No adapter may implement deposits, withdrawals or fund transfers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

OPEN_STATUSES = ("PENDING", "PARTIAL")


@dataclass
class OrderRequest:
    client_order_id: str
    symbol: str
    side: str                 # BUY | SELL
    order_type: str           # MARKET | STOP | LIMIT
    quantity: float
    purpose: str              # entry | exit | stop | target | flatten
    created_for_date: str     # the session date the decision was made on
    strategy: str | None = None
    reason: str | None = None
    limit_price: float | None = None
    stop_price: float | None = None
    est_price: float | None = None
    reserved_cash: float = 0.0
    stop_distance: float | None = None


class BrokerError(RuntimeError):
    pass


class BrokerAdapter(ABC):
    name = "base"
    is_live = False

    @abstractmethod
    def submit_order(self, req: OrderRequest) -> dict: ...

    @abstractmethod
    def cancel_order(self, order_id: str, reason: str) -> None: ...

    @abstractmethod
    def get_order(self, order_id: str) -> dict | None: ...

    @abstractmethod
    def find_by_client_id(self, client_order_id: str) -> dict | None: ...

    @abstractmethod
    def open_orders(self, symbol: str | None = None) -> list[dict]: ...

    @abstractmethod
    def account_state(self): ...

    @abstractmethod
    def is_connected(self) -> bool: ...
