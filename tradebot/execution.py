"""Order fill simulation against daily bars.

Used by BOTH the backtester and the local paper broker so they behave the
same. Assumptions (clearly approximate - daily bars cannot show the true
intraday sequence):

* MARKET orders created after a close execute at the next session's OPEN,
  worsened by half the assumed spread plus slippage.
* Sell STOP orders: if the session opens at/below the stop, the fill is at
  the OPEN (a gap - worse than the stop); otherwise, if the low touches the
  stop, the fill is at the stop minus stop-slippage.
* Sell LIMIT orders fill only if the high trades through the limit.
* If a stop and a limit could both have triggered in one bar, the stop is
  assumed to have happened first (the conservative choice).
* Orders cannot fill for more than a set share of the bar's volume; the
  rest stays unfilled. Bars with zero volume give no fills.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class SimFill:
    quantity: float
    price: float
    reference_price: float   # the "clean" price before spread/slippage
    note: str


def volume_cap(quantity: float, bar_volume: float, max_pct_of_bar_volume: float) -> float:
    if bar_volume <= 0:
        return 0.0
    cap = math.floor(bar_volume * max_pct_of_bar_volume / 100)
    return float(min(quantity, cap))


def simulate_market(side: str, quantity: float, bar: dict, impact_bps: float,
                    max_pct_of_bar_volume: float) -> SimFill | None:
    q = volume_cap(quantity, bar["volume"], max_pct_of_bar_volume)
    if q <= 0:
        return None
    ref = bar["open"]
    adj = impact_bps / 10_000
    price = ref * (1 + adj) if side == "BUY" else ref * (1 - adj)
    # A fill can't be outside the bar's range in reality; clamp to it.
    price = min(max(price, bar["low"]), bar["high"])
    note = "market order at next open" + (" (partial: volume cap)" if q < quantity else "")
    note += f"; +/-{impact_bps:.0f}bp spread & slippage assumed"
    return SimFill(q, price, ref, note)


def simulate_sell_stop(quantity: float, stop: float, bar: dict, impact_bps: float,
                       max_pct_of_bar_volume: float) -> SimFill | None:
    if bar["volume"] <= 0:
        return None
    if bar["open"] <= stop:
        ref, why = bar["open"], f"GAP: opened {bar['open']:.4f} below stop {stop:.4f}; filled at open"
    elif bar["low"] <= stop:
        ref, why = stop, f"stop {stop:.4f} touched (low {bar['low']:.4f})"
    else:
        return None
    # Protective stops are not volume-capped below the full position size in
    # this model, but are filled with extra slippage.
    q = float(quantity)
    price = max(ref * (1 - impact_bps / 10_000), bar["low"])
    return SimFill(q, price, ref, why + f"; -{impact_bps:.0f}bp slippage assumed")


def simulate_sell_limit(quantity: float, limit: float, bar: dict, half_spread_bps: float,
                        max_pct_of_bar_volume: float) -> SimFill | None:
    q = volume_cap(quantity, bar["volume"], max_pct_of_bar_volume)
    if q <= 0:
        return None
    if bar["open"] >= limit:
        ref = bar["open"]
        price = ref * (1 - half_spread_bps / 10_000)
        price = max(price, limit)
        return SimFill(q, price, ref, f"limit {limit:.4f}: opened above, filled near open")
    # require trading through the limit by the half-spread
    if bar["high"] >= limit * (1 + half_spread_bps / 10_000):
        return SimFill(q, limit, limit, f"limit {limit:.4f} reached (high {bar['high']:.4f})")
    return None
