from datetime import date

import pandas as pd

from tradebot.data.validation import check_overlap, validate_bars


def bars(rows):
    return pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "adj_close", "volume"])


def test_duplicates_missing_and_bad_rows_removed():
    df = bars([
        (date(2026, 10, 12), 10, 11, 9, 10.5, 10.5, 100),
        (date(2026, 10, 12), 10, 11, 9, 10.6, 10.6, 100),   # duplicate, keep last
        (date(2026, 10, 13), None, 11, 9, 10.5, 10.5, 100),  # missing open
        (date(2026, 10, 14), 10, 11, 9, -1, -1, 100),        # negative close
        (date(2026, 10, 15), 10, 11, 9, 10.2, 10.2, None),   # missing volume -> 0
    ])
    rep = validate_bars(df)
    assert list(rep.clean["date"]) == [date(2026, 10, 12), date(2026, 10, 15)]
    assert rep.clean.iloc[0]["close"] == 10.6
    assert rep.clean.iloc[1]["volume"] == 0
    assert any("duplicate" in m for _, m in rep.issues)


def test_incomplete_future_and_weekend_bars_dropped():
    df = bars([
        (date(2026, 10, 16), 10, 11, 9, 10, 10, 1),
        (date(2026, 10, 17), 10, 11, 9, 10, 10, 1),  # Saturday
        (date(2026, 10, 19), 10, 11, 9, 10, 10, 1),  # after last complete session
    ])
    rep = validate_bars(df, last_complete=date(2026, 10, 16))
    assert list(rep.clean["date"]) == [date(2026, 10, 16)]


def test_pence_pound_unit_error_repaired():
    rows = [(date(2026, 10, d), 100, 101, 99, 100, 100, 1) for d in (12, 13, 14, 15, 16)]
    rows[2] = (date(2026, 10, 14), 1.0, 1.01, 0.99, 1.0, 1.0, 1)  # reported in pounds instead of pence
    rep = validate_bars(bars(rows))
    assert rep.clean["close"].between(99, 101).all()
    assert any("100x" in m for _, m in rep.issues)


def test_suspicious_move_flagged_not_hidden():
    rows = [(date(2026, 10, d), 100, 101, 99, 100, 100, 1) for d in (12, 13, 14, 15)]
    rows.append((date(2026, 10, 16), 50, 51, 49, 50, 50, 1))  # -50% and stays
    rep = validate_bars(bars(rows), suspicious_move_pct=35)
    assert rep.suspicious
    assert len(rep.clean) == 5


def test_inconsistent_high_low_widened():
    rep = validate_bars(bars([(date(2026, 10, 16), 10, 9.5, 9, 10.2, 10.2, 1)]))
    r = rep.clean.iloc[0]
    assert r["high"] >= max(r["open"], r["close"])


def test_overlap_check():
    a = bars([(date(2026, 10, d), 1, 1, 1, 10.0, 10.0, 1) for d in (12, 13, 14)])
    b = a.copy()
    assert check_overlap(a, b)[0]
    b["close"] = b["close"] / 2  # e.g. 2:1 split restated history
    assert not check_overlap(a, b)[0]
