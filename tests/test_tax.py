from datetime import date

import pytest

from tradebot.portfolio.tax import compute_disposals, summary_by_tax_year, tax_year


def tx(t, d, q, cash, sym="AAA.L", price=None):
    return {"type": t, "trade_date": d, "symbol": sym, "quantity": q, "cash_delta_gbp": cash, "price": price}


def test_tax_year_boundaries():
    assert tax_year(date(2026, 4, 5)) == 2025
    assert tax_year(date(2026, 4, 6)) == 2026


def test_section_104_pool_average_cost():
    txs = [tx("BUY", "2025-05-01", 100, -1000), tx("BUY", "2025-07-01", 100, -1400),
           tx("SELL", "2025-12-01", 50, 800)]
    d = compute_disposals(txs)
    assert len(d) == 1 and d[0].rule == "Section 104 pool"
    assert d[0].cost_gbp == pytest.approx(600)          # 50 x average £12
    assert d[0].gain_gbp == pytest.approx(200)


def test_same_day_rule_takes_priority():
    txs = [tx("BUY", "2025-05-01", 100, -1000), tx("BUY", "2025-08-01", 10, -200), tx("SELL", "2025-08-01", 10, 210)]
    d = compute_disposals(txs)
    assert d[0].rule == "same day" and d[0].cost_gbp == pytest.approx(200)


def test_thirty_day_bed_and_breakfast_rule():
    # Sell then buy back within 30 days: the disposal matches the NEW purchase, not the pool
    txs = [tx("BUY", "2025-05-01", 100, -1000), tx("SELL", "2025-09-01", 100, 800),
           tx("BUY", "2025-09-20", 100, -850)]
    d = compute_disposals(txs)
    assert len(d) == 1 and d[0].rule.startswith("30-day")
    assert d[0].gain_gbp == pytest.approx(800 - 850)
    # outside 30 days -> pool
    txs[2] = tx("BUY", "2025-10-15", 100, -850)
    d2 = compute_disposals(txs)
    assert d2[0].rule == "Section 104 pool" and d2[0].gain_gbp == pytest.approx(-200)


def test_split_adjusts_pool():
    txs = [tx("BUY", "2025-05-01", 100, -1000), tx("SPLIT", "2025-06-01", 100, 0, price=2.0),
           tx("SELL", "2025-07-01", 200, 1100)]
    d = compute_disposals(txs)
    assert d[0].quantity == 200 and d[0].cost_gbp == pytest.approx(1000)


def test_summary_and_isa_note():
    txs = [tx("BUY", "2025-05-01", 100, -1000), tx("SELL", "2025-12-01", 100, 5000)]
    s = summary_by_tax_year(compute_disposals(txs), [], "gia")
    assert s[0]["tax_year"] == "2025-26" and s[0]["net_gain_gbp"] == 4000
    assert s[0]["annual_exempt_amount_gbp"] == 3000 and s[0]["net_gain_above_aea_gbp"] == 1000
    assert "Estimate" in s[0]["note"]
    assert "ISA" in summary_by_tax_year(compute_disposals(txs), [], "isa")[0]["note"]
