from datetime import date

import pytest

from tradebot.brokers.base import BrokerError, OrderRequest
from tradebot.brokers.paper import LocalPaperBroker
from tradebot.core.dataview import FrameDataView
from tradebot.costs import CostModel
from tradebot.db import Database
from tradebot.portfolio.ledger import Ledger
from conftest import make_frame

INS = {"AAA.L": {"symbol": "AAA.L", "type": "etf", "exchange": "LSE", "tier": "etf", "sector": "X"}}


@pytest.fixture
def setup(settings):
    db = Database(":memory:")
    aid = Ledger(db).create_account("local_paper", "t", 10_000, "zero_cost")
    br = LocalPaperBroker(db, aid, CostModel(settings, "zero_cost"), settings)
    df = make_frame([10, 10, 10, 10, 10], start=date(2026, 10, 12),
                    opens=[10, 10, 10.5, 9.0, 10], highs=[10.2, 10.2, 10.8, 9.5, 10.2], lows=[9.8, 9.8, 10.2, 8.5, 9.8])
    view = FrameDataView({"AAA.L": df}, INS, {"AAA.L": "GBP"})
    return db, br, view, list(df.index)


def req(cid, side="BUY", qty=100, typ="MARKET", day="2026-10-12", **kw):
    return OrderRequest(client_order_id=cid, symbol="AAA.L", side=side, order_type=typ, quantity=qty,
                        purpose=kw.pop("purpose", "entry"), created_for_date=day, **kw)


def test_submit_is_idempotent(setup):
    db, br, view, days = setup
    a = br.submit_order(req("same-id"))
    b = br.submit_order(req("same-id"))
    assert a["id"] == b["id"]
    assert db.one("SELECT COUNT(*) FROM orders")[0] == 1


def test_no_short_selling(setup):
    _, br, _, _ = setup
    with pytest.raises(BrokerError, match="no short"):
        br.submit_order(req("s1", side="SELL", purpose="exit"))


def test_never_fills_on_decision_day_then_fills_next_open(setup):
    db, br, view, days = setup
    br.submit_order(req("e1", day=days[0].isoformat()))
    assert br.process_session(days[0], view) == []        # same session as decision: no fill
    fills = br.process_session(days[1], view)
    assert len(fills) == 1 and fills[0]["price"] == pytest.approx(10 * (1 + 10 / 10_000))
    st = br.account_state()
    assert st.positions["AAA.L"].quantity == 100
    assert st.cash_gbp == pytest.approx(10_000 - 100 * 10.01, abs=0.01)


def test_stop_gap_and_oco(setup):
    db, br, view, days = setup
    br.submit_order(req("e1", day=days[0].isoformat()))
    br.process_session(days[1], view)
    br.submit_order(req("stop", side="SELL", typ="STOP", stop_price=9.6, purpose="stop", day=days[1].isoformat()))
    br.submit_order(req("exit", side="SELL", purpose="exit", day=days[2].isoformat()))
    assert br.process_session(days[2], view) == []        # stop not hit (low 10.2), exit not yet eligible
    fills = br.process_session(days[3], view)             # opens 9.0: market exit fills first, at the gap open
    assert len(fills) == 1 and fills[0]["purpose"] == "exit"
    assert br.account_state().positions == {}
    stop = br.find_by_client_id("stop")
    assert stop["status"] == "CANCELLED"                  # position gone -> stop cannot fill


def test_stop_gap_fill_price(setup):
    db, br, view, days = setup
    br.submit_order(req("e1", day=days[0].isoformat()))
    br.process_session(days[1], view)
    br.submit_order(req("stop", side="SELL", typ="STOP", stop_price=9.6, purpose="stop", day=days[1].isoformat()))
    br.process_session(days[2], view)
    fills = br.process_session(days[3], view)
    assert fills[0]["price"] < 9.6 and "GAP" in fills[0]["note"]


def test_insufficient_cash_rejected(setup):
    db, br, view, days = setup
    br.submit_order(req("big", qty=5000, day=days[0].isoformat()))  # £50,000 > £10,000
    assert br.process_session(days[1], view) == []
    assert br.find_by_client_id("big")["status"] == "REJECTED"


def test_order_expires_without_trading(setup, settings):
    db, br, view, days = setup
    view.frames["AAA.L"].loc[days[1], "volume"] = 0
    br.submit_order(req("e", day=days[0].isoformat()))
    br.process_session(days[1], view)
    assert br.find_by_client_id("e")["status"] == "EXPIRED"


def test_audit_trail_recorded(setup):
    db, br, view, days = setup
    br.submit_order(req("e1", day=days[0].isoformat()))
    br.process_session(days[1], view)
    o = br.find_by_client_id("e1")
    events = [r["status"] for r in db.query("SELECT status FROM order_events WHERE order_id=? ORDER BY id", (o["id"],))]
    assert events == ["PENDING", "FILLED"]
