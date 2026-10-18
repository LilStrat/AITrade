import sqlite3

import pytest

from tradebot.db import Database
from tradebot.portfolio.ledger import DuplicateTransaction, Ledger


@pytest.fixture
def led():
    db = Database(":memory:")
    lg = Ledger(db)
    aid = lg.create_account("local_paper", "t", 10_000, "ibkr_uk_fixed")
    return db, lg, aid


def buy(db, lg, aid, qty, price, fees, ref, day="2026-01-05", sym="AAA.L"):
    with db.tx() as c:
        lg.record_trade(c, aid, "BUY", day, sym, qty, price, "GBP", 1.0, qty * price, fees, ref, "test")


def sell(db, lg, aid, qty, price, fees, ref, day="2026-01-06", sym="AAA.L"):
    with db.tx() as c:
        lg.record_trade(c, aid, "SELL", day, sym, qty, price, "GBP", 1.0, qty * price, fees, ref, "test")


def test_cash_positions_and_realised_pnl(led):
    db, lg, aid = led
    buy(db, lg, aid, 100, 10.0, 8.0, "b1")          # cost 1000 + 8
    buy(db, lg, aid, 100, 12.0, 9.0, "b2")          # cost 1200 + 9
    st = lg.state(aid)
    assert st.cash_gbp == pytest.approx(10_000 - 2217)
    p = st.positions["AAA.L"]
    assert p.quantity == 200 and p.avg_price == pytest.approx(11.0) and p.cost_basis_gbp == pytest.approx(2217)
    sell(db, lg, aid, 50, 13.0, 3.0, "s1")          # proceeds 650 - 3
    st = lg.state(aid)
    assert st.cash_gbp == pytest.approx(10_000 - 2217 + 647)
    assert st.realised_pnl_gbp == pytest.approx(647 - 2217 / 4)
    assert st.positions["AAA.L"].quantity == 150
    sell(db, lg, aid, 150, 9.0, 3.0, "s2")
    st = lg.state(aid)
    assert "AAA.L" not in st.positions and st.closed["AAA.L"].quantity == 0
    assert st.realised_pnl_gbp == pytest.approx((647 + 1347) - 2217)
    assert st.cash_gbp == pytest.approx(10_000 + st.realised_pnl_gbp)


def test_same_event_cannot_be_booked_twice(led):
    db, lg, aid = led
    buy(db, lg, aid, 10, 10.0, 3.0, "fill:x:1")
    with pytest.raises(DuplicateTransaction):
        buy(db, lg, aid, 10, 10.0, 3.0, "fill:x:1")
    assert lg.state(aid).positions["AAA.L"].quantity == 10
    assert lg.record_dividend(aid, "2026-02-01", "AAA.L", 10, 0.5, "GBP", 1.0) is not None
    assert lg.record_dividend(aid, "2026-02-01", "AAA.L", 10, 0.5, "GBP", 1.0) is None
    assert lg.state(aid).dividends_gbp == pytest.approx(5.0)


def test_ledger_is_append_only(led):
    db, lg, aid = led
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE transactions SET cash_delta_gbp = 1e9")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM transactions")
    lg.record_correction(aid, -1.23, "fee charged twice by mistake - test")
    assert lg.state(aid).cash_gbp == pytest.approx(10_000 - 1.23)


def test_split_changes_quantity_not_value(led):
    db, lg, aid = led
    buy(db, lg, aid, 100, 10.0, 0.0, "b")
    lg.record_split(aid, "2026-03-01", "AAA.L", 2.0, 100)
    p = lg.state(aid).positions["AAA.L"]
    assert p.quantity == 200 and p.avg_price == pytest.approx(5.0) and p.cost_basis_gbp == pytest.approx(1000)


def test_overselling_is_detected(led):
    db, lg, aid = led
    buy(db, lg, aid, 10, 10.0, 0.0, "b")
    sell(db, lg, aid, 20, 10.0, 0.0, "s")
    with pytest.raises(ValueError, match="discrepancy"):
        lg.state(aid)


def test_state_persists_across_reopen(tmp_path):
    f = tmp_path / "x.sqlite3"
    db = Database(f)
    lg = Ledger(db)
    aid = lg.create_account("local_paper", "t", 5000, "ibkr_uk_fixed")
    buy(db, lg, aid, 10, 10.0, 3.0, "b")
    db.close()
    db2 = Database(f)
    st = Ledger(db2).state(aid)
    assert st.cash_gbp == pytest.approx(5000 - 103) and st.positions["AAA.L"].quantity == 10
