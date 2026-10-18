from datetime import date

import pandas as pd
import pytest

from tradebot.data.providers.base import DataProviderError, FetchResult
from tradebot.data.providers.synthetic import SyntheticProvider
from tradebot.data.providers.yahoo import YahooProvider
from tradebot.data.store import MarketData
from tradebot.db import Database
from conftest import london


class FakeTicker:
    """Mimics yfinance.Ticker.history for an LSE stock quoted in pence."""

    def __init__(self, symbol, currency="GBp"):
        self.history_metadata = {"currency": currency}

    def history(self, **kw):
        idx = pd.DatetimeIndex(pd.to_datetime(["2026-10-14", "2026-10-15", "2026-10-16"])).tz_localize("Europe/London")
        return pd.DataFrame({"Open": [250.0, 252, 254], "High": [255.0, 256, 258], "Low": [248.0, 250, 252],
                             "Close": [252.0, 254, 256], "Adj Close": [250.0, 252, 254], "Volume": [1e6, 2e6, 3e6],
                             "Dividends": [0, 5.0, 0], "Stock Splits": [0, 0, 0]}, index=idx)


def test_yahoo_pence_converted_to_pounds():
    res = YahooProvider(ticker_factory=FakeTicker).fetch_daily("VOD.L", date(2026, 10, 1), date(2026, 10, 16))
    assert res.currency == "GBP" and res.raw_currency == "GBX"
    assert res.bars["close"].tolist() == [2.52, 2.54, 2.56]
    div = [a for a in res.actions if a.type == "dividend"][0]
    assert div.value == pytest.approx(0.05) and div.date == date(2026, 10, 15)


def test_yahoo_usd_untouched():
    res = YahooProvider(ticker_factory=lambda s: FakeTicker(s, "USD")).fetch_daily("MSFT", date(2026, 10, 1), date(2026, 10, 16))
    assert res.currency == "USD" and res.bars["close"].iloc[-1] == 256


def _market(settings, primary, fallback=None):
    db = Database(":memory:")
    m = MarketData(db, settings, primary=primary, fallback=fallback or False, sleep=lambda s: None)
    if fallback is None:
        m.fallback = None
    m.sync_instruments([{"symbol": "AAA.L", "name": "A", "type": "stock", "sector": "X", "tier": "large_cap",
                         "currency": "AUTO", "exchange": "LSE", "enabled": True}])
    return m


def test_initial_and_incremental_update(settings):
    m = _market(settings, SyntheticProvider())
    r1 = m.update_symbol("AAA.L", london(2026, 10, 9))
    assert r1.ok and r1.new_bars > 2000
    r2 = m.update_symbol("AAA.L", london(2026, 10, 16))
    assert r2.ok and r2.new_bars == 5
    st = m.status("AAA.L", london(2026, 10, 16))
    assert st["fresh"] and st["source"] == "synthetic"
    # every bar has a source and a fetch timestamp
    rows = m.db.query("SELECT COUNT(*) FROM bars WHERE source IS NULL OR fetched_at IS NULL")
    assert rows[0][0] == 0


def test_stale_detection(settings):
    m = _market(settings, SyntheticProvider())
    m.update_symbol("AAA.L", london(2026, 10, 9))
    st = m.status("AAA.L", london(2026, 10, 16))  # a week later, no update
    assert not st["fresh"] and st["state"] == "stale" and st["sessions_behind"] == 5


def test_provider_failure_recorded_and_fallback_used(settings):
    class Failing(SyntheticProvider):
        name = "failing"

        def fetch_daily(self, *a, **k):
            raise DataProviderError("network down")

    m = _market(settings, Failing())
    r = m.update_symbol("AAA.L", london(2026, 10, 16))
    assert not r.ok and "network down" in r.message
    assert m.status("AAA.L", london(2026, 10, 16))["state"] == "no data"
    m2 = _market(settings, Failing(), fallback=SyntheticProvider())
    r2 = m2.update_symbol("AAA.L", london(2026, 10, 16))
    assert r2.ok and r2.source == "synthetic"


def test_retry_with_backoff(settings):
    calls = []

    class Flaky(SyntheticProvider):
        def fetch_daily(self, *a, **k):
            calls.append(1)
            if len(calls) < 3:
                raise DataProviderError("timeout")
            return super().fetch_daily(*a, **k)

    m = _market(settings, Flaky())
    assert m.update_symbol("AAA.L", london(2026, 10, 16)).ok
    assert len(calls) == 3


def test_restated_history_triggers_full_reload(settings):
    class Restating(SyntheticProvider):
        factor = 1.0

        def fetch_daily(self, *a, **k):
            r = super().fetch_daily(*a, **k)
            for c in ("open", "high", "low", "close", "adj_close"):
                r.bars[c] = r.bars[c] / self.factor
            return r

    p = Restating()
    m = _market(settings, p)
    m.update_symbol("AAA.L", london(2026, 10, 9))
    p.factor = 2.0  # provider now reports split-adjusted history
    r = m.update_symbol("AAA.L", london(2026, 10, 16))
    assert r.ok and r.restated
    first_old = m.bars("AAA.L").iloc[0]["close"]
    expected = SyntheticProvider().fetch_daily("AAA.L", date(2000, 1, 1), date(2026, 10, 16)).bars
    assert first_old == pytest.approx(float(expected[expected["date"] == m.bars("AAA.L").index[0]]["close"].iloc[0]) / 2)


def test_fallback_disagreeing_with_stored_data_rejected(settings):
    class Fails(SyntheticProvider):
        name = "primary"
        fail = False

        def fetch_daily(self, *a, **k):
            if self.fail:
                raise DataProviderError("down")
            return super().fetch_daily(*a, **k)

    class Different(SyntheticProvider):
        name = "other"

        def fetch_daily(self, *a, **k):
            r = super().fetch_daily(*a, **k)
            r.bars["close"] = r.bars["close"] * 1.5
            return r

    p = Fails()
    m = _market(settings, p, fallback=Different())
    m.update_symbol("AAA.L", london(2026, 10, 9))
    p.fail = True
    r = m.update_symbol("AAA.L", london(2026, 10, 16))
    assert not r.ok and "disagrees" in r.message


def test_incomplete_today_bar_not_stored(settings):
    m = _market(settings, SyntheticProvider())
    m.update_symbol("AAA.L", london(2026, 10, 16, 14))  # Friday 14:00, session not complete
    assert m.bars("AAA.L").index[-1] == date(2026, 10, 15)
