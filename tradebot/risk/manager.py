"""Independent risk manager.

Every proposed entry is sized and checked here before any order can exist.
This module does not import strategies or the AI layer, and nothing they
return can bypass it. A rejected proposal never becomes an order.

Note: a stop-loss limits risk only under normal conditions. Price gaps,
slippage and illiquidity can make the actual loss larger than the
"risk per trade" figure.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field


@dataclass
class Check:
    name: str
    passed: bool
    detail: str
    warning: bool = False


@dataclass
class Proposal:
    symbol: str
    strategy: str
    side: str                     # only 'BUY' entries are proposed in v1
    entry_est: float              # local currency
    stop_distance: float          # local currency
    currency: str
    fx_to_gbp: float | None
    instrument: dict
    avg_volume_20: float
    avg_value_gbp_20: float
    sector: str


@dataclass
class HeldItem:
    symbol: str
    value_gbp: float
    sector: str


@dataclass
class RiskContext:
    mode: str
    equity_gbp: float
    cash_gbp: float
    reserved_gbp: float
    held: list[HeldItem]
    pending: list[HeldItem]       # open BUY orders not yet filled
    global_blocks: list[str]      # reasons that block ALL new entries
    symbol_data_fresh: bool
    symbol_data_note: str
    correlated_with: list[str] = field(default_factory=list)


@dataclass
class RiskDecision:
    approved: bool
    quantity: int
    checks: list[Check]
    value_gbp: float = 0.0
    risk_gbp: float = 0.0
    est_round_trip_cost_gbp: float = 0.0
    reserved_gbp: float = 0.0

    @property
    def rejections(self) -> list[str]:
        return [c.detail for c in self.checks if not c.passed]

    @property
    def warnings(self) -> list[str]:
        return [c.detail for c in self.checks if c.passed and c.warning]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["rejections"] = self.rejections
        d["warnings"] = self.warnings
        return d


class RiskManager:
    def __init__(self, settings: dict, cost_model):
        self.r = settings["risk"]
        self.u = settings["universe"]
        self.x = settings["execution"]
        self.costs = cost_model
        if self.r.get("allow_short") or self.r.get("allow_leverage"):
            raise ValueError("short selling and leverage are not supported")

    def _warn_level(self, used: float, limit: float) -> bool:
        return limit > 0 and used >= limit * float(self.r["warn_at_pct_of_limit"]) / 100

    def evaluate(self, p: Proposal, ctx: RiskContext) -> RiskDecision:
        r = self.r
        checks: list[Check] = []

        def add(name, ok, detail, warning=False):
            checks.append(Check(name, bool(ok), detail, warning))

        # ---- hard gates ---------------------------------------------------
        add("mode", ctx.mode in ("local_paper", "backtest"),
            f"Operating mode '{ctx.mode}' may place orders" if ctx.mode in ("local_paper", "backtest")
            else f"Mode '{ctx.mode}' is not authorised to place orders")
        for b in ctx.global_blocks:
            add("trading_block", False, b)
        add("long_only", p.side == "BUY", "Long-only entry" if p.side == "BUY" else "Short selling is disabled")
        itype = p.instrument.get("type")
        add("instrument_type", itype in ("stock", "etf"), f"Instrument type '{itype}' allowed"
            if itype in ("stock", "etf") else f"Instrument type '{itype}' not allowed")
        add("data_fresh", ctx.symbol_data_fresh, ctx.symbol_data_note)
        add("fx_known", p.fx_to_gbp is not None and p.fx_to_gbp > 0,
            "Currency conversion rate available" if p.fx_to_gbp else f"No reliable {p.currency}->GBP rate")
        add("equity_known", ctx.equity_gbp > 0, f"Equity £{ctx.equity_gbp:,.2f}")
        held_syms = {h.symbol for h in ctx.held}
        pend_syms = {h.symbol for h in ctx.pending}
        add("no_duplicate", p.symbol not in held_syms and p.symbol not in pend_syms,
            "No existing position or pending order in this instrument"
            if p.symbol not in held_syms | pend_syms else "Already holding or ordering this instrument (duplicate)")
        if any(not c.passed for c in checks):
            return RiskDecision(False, 0, checks)

        fx = float(p.fx_to_gbp)
        price_gbp = p.entry_est * fx
        stop_pct = p.stop_distance / p.entry_est * 100 if p.entry_est > 0 else 0
        add("stop_distance", r["min_stop_distance_pct"] <= stop_pct <= r["max_stop_distance_pct"],
            f"Stop {stop_pct:.2f}% below entry (allowed {r['min_stop_distance_pct']}-{r['max_stop_distance_pct']}%)")
        add("min_price", price_gbp >= float(self.u["min_price_gbp"]),
            f"Price £{price_gbp:.4f} (minimum £{self.u['min_price_gbp']})")
        add("liquidity", p.avg_value_gbp_20 >= float(self.u["min_avg_daily_value_gbp"]),
            f"Average daily traded value £{p.avg_value_gbp_20:,.0f} (minimum £{self.u['min_avg_daily_value_gbp']:,.0f})")
        n_open = len(held_syms | pend_syms)
        add("max_positions", n_open < int(r["max_open_positions"]),
            f"{n_open} open/pending positions (max {r['max_open_positions']})",
            self._warn_level(n_open + 1, int(r["max_open_positions"])))
        add("correlation", len(ctx.correlated_with) < int(r["max_correlated_positions"]),
            f"Correlated (>{r['correlation_threshold']}) with {len(ctx.correlated_with)} holding(s)"
            + (f": {', '.join(ctx.correlated_with)}" if ctx.correlated_with else "")
            + f" (max {r['max_correlated_positions']})")
        if any(not c.passed for c in checks):
            return RiskDecision(False, 0, checks)

        # ---- sizing ------------------------------------------------------
        eq = ctx.equity_gbp
        risk_budget = eq * float(r["max_risk_per_trade_pct"]) / 100
        stop_gbp = p.stop_distance * fx
        buffer = 1 + float(self.x["reserve_buffer_pct"]) / 100
        reserve_floor = eq * float(r["min_cash_reserve_pct"]) / 100
        spendable = ctx.cash_gbp - ctx.reserved_gbp - reserve_floor
        limits = {
            "risk per trade": math.floor(risk_budget / stop_gbp) if stop_gbp > 0 else 0,
            "max position size": math.floor(eq * float(r["max_position_pct"]) / 100 / price_gbp),
            "available cash": math.floor(max(spendable, 0) / (price_gbp * buffer * 1.006)),
            "liquidity (share of average volume)": math.floor(p.avg_volume_20 * float(r["max_pct_of_avg_volume"]) / 100),
        }
        qty = int(max(0, min(limits.values())))
        binding = min(limits, key=limits.get)
        add("size", qty >= 1, f"Size {qty} shares (limited by {binding}; limits: "
            + ", ".join(f"{k} {v}" for k, v in limits.items()) + ")")
        if qty < 1:
            return RiskDecision(False, 0, checks)

        value = qty * price_gbp
        risk_amt = qty * stop_gbp
        rt_cost = self.costs.round_trip_estimate(qty, p.entry_est, p.currency, fx, p.instrument)
        buy_fees = self.costs.fees("BUY", qty, p.entry_est, p.currency, fx, p.instrument).total_gbp
        reserved = value * buffer + buy_fees
        cost_ratio = rt_cost / risk_amt * 100 if risk_amt > 0 else 999
        add("cost_vs_risk", cost_ratio <= float(r["max_cost_pct_of_risk"]),
            f"Estimated round-trip costs £{rt_cost:.2f} = {cost_ratio:.0f}% of the £{risk_amt:.2f} at risk "
            f"(max {r['max_cost_pct_of_risk']}%)")

        invested = sum(h.value_gbp for h in ctx.held) + sum(h.value_gbp for h in ctx.pending)
        exp_after = (invested + value) / eq * 100
        add("total_exposure", exp_after <= float(r["max_total_exposure_pct"]),
            f"Exposure after trade {exp_after:.1f}% of equity (max {r['max_total_exposure_pct']}%)",
            self._warn_level(exp_after, float(r["max_total_exposure_pct"])))
        sector_val = sum(h.value_gbp for h in ctx.held + ctx.pending if h.sector == p.sector) + value
        sector_pct = sector_val / eq * 100
        add("sector_concentration", sector_pct <= float(r["max_sector_pct"]),
            f"Sector '{p.sector}' would be {sector_pct:.1f}% of equity (max {r['max_sector_pct']}%)",
            self._warn_level(sector_pct, float(r["max_sector_pct"])))
        add("cash", reserved <= ctx.cash_gbp - ctx.reserved_gbp,
            f"Reserves £{reserved:,.2f} of £{ctx.cash_gbp - ctx.reserved_gbp:,.2f} available cash")

        ok = all(c.passed for c in checks)
        return RiskDecision(ok, qty if ok else 0, checks, value, risk_amt, rt_cost, reserved if ok else 0.0)
