"""UK Capital Gains Tax RECORD-KEEPING and ESTIMATES for shares/ETFs.

IMPORTANT: this is an estimate to help you keep records. It is NOT a tax
calculation you can rely on and does not determine your tax liability.
Tax treatment depends on your circumstances (income, other gains, losses
brought forward, residency, whether HMRC regards the activity as trading,
reporting-fund status of ETFs, etc.). Check GOV.UK or a qualified adviser.

Implemented share identification rules (HMRC, for individuals), per
instrument:
  1. same-day rule: disposals are matched with acquisitions on the same day
  2. 30-day rule ("bed and breakfasting"): then with acquisitions in the 30
     days AFTER the disposal (earliest first)
  3. Section 104 pool: then with the pooled holding at average cost
Allowable costs include purchase price plus buying costs; proceeds are net
of selling costs. Known limitations: splits during a 30-day window and
non-GBP acquisition-date FX nuances are simplified; accumulation-ETF
notional distributions and excess reportable income are NOT handled.

Accounts held in an ISA are outside UK CGT and dividend tax.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

# Annual exempt amount by tax year start (verify each year on GOV.UK).
ANNUAL_EXEMPT_AMOUNT = {2022: 12300, 2023: 6000, 2024: 3000, 2025: 3000, 2026: 3000}
DIVIDEND_ALLOWANCE = {2022: 2000, 2023: 1000, 2024: 500, 2025: 500, 2026: 500}


def tax_year(d: date) -> int:
    """Start year of the UK tax year containing ``d`` (6 April - 5 April)."""
    return d.year if (d.month, d.day) >= (4, 6) else d.year - 1


def tax_year_label(y: int) -> str:
    return f"{y}-{str(y + 1)[-2:]}"


@dataclass
class Disposal:
    symbol: str
    date: date
    quantity: float
    proceeds_gbp: float
    cost_gbp: float
    rule: str

    @property
    def gain_gbp(self) -> float:
        return self.proceeds_gbp - self.cost_gbp


def compute_disposals(transactions: list[dict]) -> list[Disposal]:
    by_sym: dict[str, list[dict]] = defaultdict(list)
    for t in transactions:
        if t["type"] in ("BUY", "SELL", "SPLIT") and t["symbol"]:
            by_sym[t["symbol"]].append(t)
    out: list[Disposal] = []
    for sym, txs in by_sym.items():
        out.extend(_disposals_for(sym, txs))
    out.sort(key=lambda d: (d.date, d.symbol))
    return out


def _disposals_for(sym: str, txs: list[dict]) -> list[Disposal]:
    buys: dict[date, list[float]] = defaultdict(lambda: [0.0, 0.0])   # qty, cost
    sells: dict[date, list[float]] = defaultdict(lambda: [0.0, 0.0])  # qty, proceeds
    splits: list[tuple[date, float]] = []
    for t in txs:
        d = date.fromisoformat(t["trade_date"])
        if t["type"] == "BUY":
            buys[d][0] += t["quantity"]
            buys[d][1] += -t["cash_delta_gbp"]          # gross + fees
        elif t["type"] == "SELL":
            sells[d][0] += t["quantity"]
            sells[d][1] += t["cash_delta_gbp"]          # gross - fees
        elif t["type"] == "SPLIT" and t["price"]:
            splits.append((d, float(t["price"])))
    rem_buy = {d: [q, c] for d, (q, c) in buys.items()}
    rem_sell = {d: [q, p] for d, (q, p) in sells.items()}
    out: list[Disposal] = []

    def take(pool: list[float], q: float) -> float:
        cost = pool[1] * (q / pool[0]) if pool[0] else 0.0
        pool[0] -= q
        pool[1] -= cost
        return cost

    # 1. same day
    for d in sorted(rem_sell):
        if d in rem_buy and rem_sell[d][0] > 0 and rem_buy[d][0] > 0:
            q = min(rem_sell[d][0], rem_buy[d][0])
            proceeds = take(rem_sell[d], q)
            out.append(Disposal(sym, d, q, proceeds, take(rem_buy[d], q), "same day"))
    # 2. thirty days after
    for d in sorted(rem_sell):
        for bd in sorted(b for b in rem_buy if d < b <= d + timedelta(days=30)):
            if rem_sell[d][0] <= 1e-9:
                break
            if rem_buy[bd][0] <= 1e-9:
                continue
            q = min(rem_sell[d][0], rem_buy[bd][0])
            proceeds = take(rem_sell[d], q)
            out.append(Disposal(sym, d, q, proceeds, take(rem_buy[bd], q), f"30-day rule (bought {bd})"))
    # 3. section 104 pool
    pool = [0.0, 0.0]
    events = sorted(set(rem_buy) | set(rem_sell) | {d for d, _ in splits})
    split_map = dict(splits)
    for d in events:
        if d in split_map:
            pool[0] *= split_map[d]
        if d in rem_buy and rem_buy[d][0] > 1e-9:
            pool[0] += rem_buy[d][0]
            pool[1] += rem_buy[d][1]
        if d in rem_sell and rem_sell[d][0] > 1e-9:
            q = min(rem_sell[d][0], pool[0])
            if q > 1e-9:
                proceeds = take(rem_sell[d], q)
                out.append(Disposal(sym, d, q, proceeds, take(pool, q), "Section 104 pool"))
    return out


def summary_by_tax_year(disposals: list[Disposal], dividends: list[dict], account_type: str = "gia") -> list[dict]:
    years: dict[int, dict] = {}
    for dp in disposals:
        y = tax_year(dp.date)
        e = years.setdefault(y, {"gains": 0.0, "losses": 0.0, "proceeds": 0.0, "disposals": 0, "dividends": 0.0})
        e["proceeds"] += dp.proceeds_gbp
        e["disposals"] += 1
        if dp.gain_gbp >= 0:
            e["gains"] += dp.gain_gbp
        else:
            e["losses"] += -dp.gain_gbp
    for dv in dividends:
        y = tax_year(date.fromisoformat(dv["trade_date"]))
        years.setdefault(y, {"gains": 0.0, "losses": 0.0, "proceeds": 0.0, "disposals": 0, "dividends": 0.0})
        years[y]["dividends"] += dv["cash_delta_gbp"]
    out = []
    for y in sorted(years):
        e = years[y]
        net = e["gains"] - e["losses"]
        aea = ANNUAL_EXEMPT_AMOUNT.get(y, ANNUAL_EXEMPT_AMOUNT[max(ANNUAL_EXEMPT_AMOUNT)])
        out.append({
            "tax_year": tax_year_label(y), "disposals": e["disposals"], "total_proceeds_gbp": round(e["proceeds"], 2),
            "gains_gbp": round(e["gains"], 2), "losses_gbp": round(e["losses"], 2), "net_gain_gbp": round(net, 2),
            "annual_exempt_amount_gbp": aea, "net_gain_above_aea_gbp": round(max(net - aea, 0), 2),
            "dividends_gbp": round(e["dividends"], 2),
            "dividend_allowance_gbp": DIVIDEND_ALLOWANCE.get(y, DIVIDEND_ALLOWANCE[max(DIVIDEND_ALLOWANCE)]),
            "note": ("ISA account: gains and dividends are outside UK tax" if account_type == "isa" else
                     "Estimate only. Reporting may be required if disposal proceeds exceed HMRC's threshold "
                     "(£50,000 from 2023-24) even with no tax due - check GOV.UK."),
        })
    return out
