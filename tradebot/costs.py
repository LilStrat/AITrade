"""Trading cost estimates (commission, UK stamp duty, PTM levy, FX, spread).

These are ESTIMATES based on published tariffs and configurable in
settings. They are not a quote from any broker.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class CostBreakdown:
    commission_gbp: float = 0.0
    stamp_duty_gbp: float = 0.0
    other_fees_gbp: float = 0.0      # PTM levy etc.
    fx_cost_gbp: float = 0.0

    @property
    def total_gbp(self) -> float:
        return self.commission_gbp + self.stamp_duty_gbp + self.other_fees_gbp + self.fx_cost_gbp

    def to_dict(self) -> dict:
        return {**asdict(self), "total_gbp": self.total_gbp}


def _r2(x: float) -> float:
    return round(x + 0.0, 2)


class CostModel:
    def __init__(self, settings: dict, profile: str | None = None):
        c = settings["costs"]
        self.c = c
        key = profile or settings["paper"]["cost_profile"]
        self.profile_key = key
        self.p = c["profiles"][key]

    @property
    def label(self) -> str:
        return self.p.get("label", self.profile_key)

    # ------------------------------------------------------------- price impact
    def half_spread_bps(self, instrument: dict) -> float:
        tiers = self.c["spread_bps"]
        if instrument.get("type") == "etf":
            return float(tiers.get("etf", tiers["default"]))
        return float(tiers.get(instrument.get("tier", "default"), tiers["default"]))

    def market_impact_bps(self, instrument: dict) -> float:
        return self.half_spread_bps(instrument) + float(self.c["slippage_bps"])

    def stop_impact_bps(self, instrument: dict) -> float:
        return self.half_spread_bps(instrument) + float(self.c["stop_slippage_bps"])

    # ------------------------------------------------------------- explicit fees
    @staticmethod
    def is_uk_share(instrument: dict) -> bool:
        return instrument.get("type") == "stock" and instrument.get("exchange", "LSE") == "LSE"

    def fees(self, side: str, quantity: float, price: float, currency: str, fx_to_gbp: float,
             instrument: dict) -> CostBreakdown:
        """Explicit charges for one fill. ``price`` is in ``currency`` (major unit)."""
        value_local = quantity * price
        value_gbp = value_local * fx_to_gbp
        b = CostBreakdown()
        if currency == "GBP":
            pct = float(self.p["gbp_commission_pct"]) / 100
            b.commission_gbp = max(float(self.p["gbp_commission_min"]), value_gbp * pct) if pct or self.p["gbp_commission_min"] else 0.0
        else:
            per_share = float(self.p["usd_per_share"]) * quantity
            comm = max(float(self.p["usd_commission_min"]), per_share) if per_share or self.p["usd_commission_min"] else 0.0
            cap = float(self.p["usd_commission_max_pct"]) / 100 * value_local
            if cap > 0:
                comm = min(comm, cap)
            b.commission_gbp = comm * fx_to_gbp
            b.fx_cost_gbp = value_gbp * float(self.p["fx_fee_pct"]) / 100
        if self.is_uk_share(instrument):
            if side == "BUY":
                b.stamp_duty_gbp = value_gbp * float(self.c["stamp_duty_pct"]) / 100
            if value_gbp > float(self.c["ptm_levy_threshold_gbp"]):
                b.other_fees_gbp = float(self.c["ptm_levy_gbp"])
        return CostBreakdown(_r2(b.commission_gbp), _r2(b.stamp_duty_gbp), _r2(b.other_fees_gbp), _r2(b.fx_cost_gbp))

    def round_trip_estimate(self, quantity: float, price: float, currency: str, fx_to_gbp: float,
                            instrument: dict) -> float:
        """Estimated GBP cost of buying and later selling (fees + spread/slippage)."""
        buy = self.fees("BUY", quantity, price, currency, fx_to_gbp, instrument).total_gbp
        sell = self.fees("SELL", quantity, price, currency, fx_to_gbp, instrument).total_gbp
        impact = 2 * quantity * price * fx_to_gbp * self.market_impact_bps(instrument) / 10_000
        return buy + sell + impact
