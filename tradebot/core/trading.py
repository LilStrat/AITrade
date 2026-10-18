"""Trading core - the ONE daily cycle used by both backtests and paper trading.

For each trading session ``day``:

  process_session(day)
     1. fill orders decided on earlier days (market orders at the open,
        then resting stops/limits), using the shared execution model
     2. apply corporate actions (dividends credited on the ex-date; splits)
     3. value the portfolio at the close, update daily-loss / drawdown state

  decide(day)               - only for the latest session in live paper mode
     4. reconcile / health checks; anything uncertain blocks new entries
     5. exits and trailing-stop updates for open positions
     6. entry signals from enabled strategies -> risk manager -> orders that
        will execute at the NEXT session's open
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

from ..brokers.base import OrderRequest
from ..brokers.paper import LocalPaperBroker
from ..db import Database
from ..notifications import Alerts
from ..portfolio.ledger import AccountState
from ..risk.manager import HeldItem, Proposal, RiskContext, RiskManager
from ..strategies.base import IndicatorTable, PositionView, Strategy
from ..strategies.regime import RegimeFilter

log = logging.getLogger(__name__)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class DayResult:
    day: date
    equity_gbp: float = 0.0
    fills: list[dict] = field(default_factory=list)
    signals: list[dict] = field(default_factory=list)
    orders: list[dict] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class TradingCore:
    def __init__(self, db: Database, settings: dict, data, broker: LocalPaperBroker, strategies: list[Strategy],
                 risk: RiskManager, alerts: Alerts, mode: str = "local_paper", benchmark: str | None = None,
                 apply_splits: bool = True, record_signals: bool = True, auto_resume_drawdown_after: int | None = None):
        self.db = db
        self.s = settings
        self.data = data
        self.broker = broker
        self.account_id = broker.account_id
        self.strategies = {st.key: st for st in strategies}
        self.risk = risk
        self.alerts = alerts
        self.mode = mode
        self.benchmark = benchmark or settings["universe"]["benchmark"]
        self.apply_splits = apply_splits
        self.record_signals = record_signals
        self.auto_resume_after = auto_resume_drawdown_after
        self._paused_sessions = 0
        self.ind: dict[tuple[str, str], IndicatorTable] = {}
        self.regime = RegimeFilter(data.history(self.benchmark))
        self._returns: dict[str, pd.Series] = {}
        self.drawdown_pauses = 0
        self.daily_loss_pauses = 0

    # ------------------------------------------------------------ preparation
    def prepare(self, symbols: list[str], from_day: date | None = None) -> None:
        for sym in symbols:
            h = self.data.history(sym)
            if h.empty:
                continue
            for key, st in self.strategies.items():
                self.ind[(key, sym)] = IndicatorTable(st.prepare(h), from_day)
            self._returns[sym] = h["close"].pct_change()

    # ------------------------------------------------------------ risk state
    def risk_state(self) -> dict:
        r = self.db.one("SELECT * FROM risk_state WHERE account_id=?", (self.account_id,))
        return dict(r) if r else {}

    def _update_risk_state(self, **fields) -> None:
        sets = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE risk_state SET {sets}, updated_at=? WHERE account_id=?",
                        (*fields.values(), utcnow(), self.account_id))

    # ------------------------------------------------------------ valuation
    def valuation(self, state: AccountState, day: date) -> tuple[float, float, list[str]]:
        """(positions value GBP, unrealised P&L GBP, problems)"""
        total = unreal = 0.0
        problems = []
        for sym, p in state.positions.items():
            lc = self.data.last_close(sym, day)
            fx = self.data.fx_to_gbp(self.data.currency(sym), day)
            if lc is None or fx is None:
                problems.append(f"cannot value {sym} on {day}")
                value = p.cost_basis_gbp  # fall back to cost, flagged
            else:
                value = p.quantity * lc[0] * fx
                if (day - lc[1]).days > 5:
                    problems.append(f"{sym} valued with an old price from {lc[1]}")
            total += value
            unreal += value - p.cost_basis_gbp
        return total, unreal, problems

    # ------------------------------------------------------------ session
    def process_session(self, day: date) -> DayResult:
        res = DayResult(day)
        res.fills = self.broker.process_session(day, self.data, on_fill=lambda o, f: self._on_fill(o, f, day))
        self._corporate_actions(day)
        state = self.broker.account_state()
        pos_val, unreal, problems = self.valuation(state, day)
        equity = state.cash_gbp + pos_val
        res.equity_gbp = equity
        for p in problems:
            res.warnings.append(p)
            self.alerts.raise_("warning", "valuation", p)
        self.db.execute("""INSERT INTO equity_snapshots(account_id,date,cash_gbp,positions_gbp,equity_gbp,
                               realised_pnl_gbp,unrealised_pnl_gbp) VALUES (?,?,?,?,?,?,?)
                           ON CONFLICT(account_id,date) DO UPDATE SET cash_gbp=excluded.cash_gbp,
                             positions_gbp=excluded.positions_gbp, equity_gbp=excluded.equity_gbp,
                             realised_pnl_gbp=excluded.realised_pnl_gbp, unrealised_pnl_gbp=excluded.unrealised_pnl_gbp""",
                        (self.account_id, day.isoformat(), state.cash_gbp, round(pos_val, 2), round(equity, 2),
                         round(state.realised_pnl_gbp, 2), round(unreal, 2)))
        self._update_position_meta(state, day)
        self._update_limits(day, equity, res)
        return res

    def _on_fill(self, order: dict, fill: dict, day: date) -> None:
        sym = order["symbol"]
        if order["side"] == "BUY" and order["purpose"] == "entry":
            stop_dist = order["stop_distance"] or 0
            stop = fill["price"] - stop_dist
            held = self.broker.account_state().positions.get(sym)
            qty = held.quantity if held else fill["quantity"]
            self.db.execute("""INSERT INTO position_meta(account_id,symbol,strategy,entry_date,initial_stop,current_stop,
                                   highest_close,bars_held) VALUES (?,?,?,?,?,?,?,0)
                               ON CONFLICT(account_id,symbol) DO UPDATE SET current_stop=excluded.current_stop""",
                            (self.account_id, sym, order["strategy"], day.isoformat(), stop, stop, fill["price"]))
            if stop > 0:
                self.broker.submit_order(OrderRequest(
                    client_order_id=f"{self.account_id}:{sym}:stop:{day.isoformat()}:{order['id'][:8]}",
                    symbol=sym, side="SELL", order_type="STOP", quantity=qty, stop_price=round(stop, 6),
                    purpose="stop", created_for_date=day.isoformat(), strategy=order["strategy"],
                    reason=f"Protective stop {stop:.4f} ({stop_dist:.4f} below fill {fill['price']:.4f})"))
            self.alerts.raise_("info", "fill", f"[PAPER] BOUGHT {fill['quantity']:g} {sym} @ {fill['price']:.4f} "
                                               f"(simulated fill, {fill['note']})", dedupe_hours=0)
        elif order["side"] == "SELL":
            remaining = self.broker.account_state().positions.get(sym)
            if remaining is None or remaining.quantity <= 1e-9:
                self.broker.cancel_open_sells(sym, f"position closed by {order['purpose']} order", except_id=order["id"])
                self.db.execute("DELETE FROM position_meta WHERE account_id=? AND symbol=?", (self.account_id, sym))
            kind = "STOP-LOSS" if order["purpose"] == "stop" else order["purpose"].upper()
            self.alerts.raise_("info" if order["purpose"] != "stop" else "warning", "fill",
                               f"[PAPER] SOLD {fill['quantity']:g} {sym} @ {fill['price']:.4f} ({kind}; {fill['note']})",
                               dedupe_hours=0)

    def _corporate_actions(self, day: date) -> None:
        state = self.broker.account_state()
        for sym, p in list(state.positions.items()):
            for a in self.data.actions_on(sym, day):
                if a["type"] == "dividend":
                    ccy = a.get("currency") or self.data.currency(sym)
                    fx = self.data.fx_to_gbp(ccy, day) or 0.0
                    wh = float(self.s["costs"]["us_dividend_withholding_pct"]) if ccy == "USD" else 0.0
                    # The position must have been held before the ex-date.
                    if p.opened and p.opened < day.isoformat() and fx:
                        self.broker.ledger.record_dividend(self.account_id, day.isoformat(), sym, p.quantity,
                                                           float(a["value"]), ccy, fx, wh)
                elif a["type"] == "split" and self.apply_splits:
                    ratio = float(a["value"])
                    if ratio > 0 and p.opened and p.opened < day.isoformat():
                        if self.broker.ledger.record_split(self.account_id, day.isoformat(), sym, ratio, p.quantity):
                            self._adjust_for_split(sym, ratio)
                            self.alerts.raise_("warning", "corporate_action",
                                               f"{sym} split {ratio:g}:1 applied to the paper position; stops adjusted")

    def _adjust_for_split(self, sym: str, ratio: float) -> None:
        self.db.execute("""UPDATE position_meta SET initial_stop=initial_stop/?, current_stop=current_stop/?,
                           highest_close=highest_close/? WHERE account_id=? AND symbol=?""",
                        (ratio, ratio, ratio, self.account_id, sym))
        held = self.broker.account_state().positions.get(sym)
        for o in self.broker.open_orders(sym):
            if o["side"] == "SELL":
                self.broker.cancel_order(o["id"], f"replaced after {ratio:g}:1 split")
                if o["order_type"] == "STOP" and held:
                    self.broker.submit_order(OrderRequest(
                        client_order_id=f"{o['client_order_id']}:split", symbol=sym, side="SELL", order_type="STOP",
                        quantity=held.quantity, stop_price=o["stop_price"] / ratio, purpose="stop",
                        created_for_date=o["created_for_date"], strategy=o["strategy"],
                        reason=f"Stop adjusted for {ratio:g}:1 split"))

    def _update_position_meta(self, state: AccountState, day: date) -> None:
        for sym in state.positions:
            bar = self.data.bar(sym, day)
            meta = self.db.one("SELECT * FROM position_meta WHERE account_id=? AND symbol=?", (self.account_id, sym))
            if meta is None or bar is None:
                continue
            if meta["entry_date"] == day.isoformat():
                self.db.execute("UPDATE position_meta SET highest_close=max(highest_close, ?) WHERE account_id=? AND symbol=?",
                                (bar["close"], self.account_id, sym))
                continue
            self.db.execute("""UPDATE position_meta SET highest_close=max(highest_close, ?), bars_held=bars_held+1
                               WHERE account_id=? AND symbol=?""", (bar["close"], self.account_id, sym))

    def _update_limits(self, day: date, equity: float, res: DayResult) -> None:
        r = self.s["risk"]
        rs = self.risk_state()
        prev = self.db.one("SELECT equity_gbp FROM equity_snapshots WHERE account_id=? AND date<? ORDER BY date DESC LIMIT 1",
                           (self.account_id, day.isoformat()))
        day_start = float(prev[0]) if prev else float(rs.get("peak_equity") or equity)
        peak = max(float(rs.get("peak_equity") or equity), equity)
        fields = {"day": day.isoformat(), "day_start_equity": day_start, "peak_equity": peak}
        daily_loss = (day_start - equity) / day_start * 100 if day_start > 0 else 0.0
        dd = (peak - equity) / peak * 100 if peak > 0 else 0.0
        if daily_loss >= float(r["max_daily_loss_pct"]):
            fields["daily_pause_day"] = day.isoformat()
            self.daily_loss_pauses += 1
            self.alerts.raise_("warning", "risk", f"Daily loss {daily_loss:.2f}% on {day} reached the "
                                                  f"{r['max_daily_loss_pct']}% limit - no new entries today")
        elif daily_loss >= float(r["max_daily_loss_pct"]) * float(r["warn_at_pct_of_limit"]) / 100:
            res.warnings.append(f"Daily loss {daily_loss:.2f}% is approaching the {r['max_daily_loss_pct']}% limit")
        if dd >= float(r["max_drawdown_pct"]) and not rs.get("paused_reason"):
            fields["paused_reason"] = (f"Drawdown {dd:.2f}% from peak £{peak:,.2f} reached the {r['max_drawdown_pct']}% "
                                       "limit. New entries paused until you review and resume.")
            self.drawdown_pauses += 1
            self._paused_sessions = 0
            self.alerts.raise_("critical", "risk", fields["paused_reason"])
        elif dd >= float(r["max_drawdown_pct"]) * float(r["warn_at_pct_of_limit"]) / 100:
            res.warnings.append(f"Drawdown {dd:.2f}% is approaching the {r['max_drawdown_pct']}% limit")
        if rs.get("paused_reason") and self.auto_resume_after is not None:
            self._paused_sessions += 1
            if self._paused_sessions >= self.auto_resume_after:   # backtests only
                fields["paused_reason"] = None
                fields["peak_equity"] = equity
        self._update_risk_state(**fields)

    # ------------------------------------------------------------ decisions
    def global_blocks(self, day: date) -> list[str]:
        rs = self.risk_state()
        blocks = []
        if rs.get("kill_switch"):
            blocks.append("EMERGENCY STOP is active - no new orders")
        if rs.get("paused_reason"):
            blocks.append(rs["paused_reason"])
        if rs.get("daily_pause_day") == day.isoformat():
            blocks.append("Daily loss limit reached today")
        if rs.get("account_uncertain"):
            blocks.append("Account state is uncertain (reconciliation failed) - resolve before trading")
        fresh, note = self.data.freshness(self.benchmark, day)
        if not fresh:
            blocks.append(f"Benchmark data not usable: {note}")
        return blocks

    def reconcile(self) -> list[str]:
        """Internal consistency checks on the paper account."""
        problems = []
        try:
            st = self.broker.account_state()
        except ValueError as exc:
            return [str(exc)]
        if st.cash_gbp < -0.01:
            problems.append(f"negative cash £{st.cash_gbp:,.2f}")
        if st.reserved_gbp > st.cash_gbp + 0.01:
            problems.append("reserved cash exceeds cash")
        for o in self.broker.open_orders():
            if o["side"] == "SELL":
                held = st.positions.get(o["symbol"])
                if held is None or o["quantity"] - o["filled_quantity"] > held.quantity + 1e-9:
                    problems.append(f"open sell order {o['client_order_id']} exceeds holding")
        metas = {r["symbol"] for r in self.db.query("SELECT symbol FROM position_meta WHERE account_id=?", (self.account_id,))}
        for sym in st.positions:
            if sym not in metas:
                problems.append(f"position {sym} has no stop/strategy record")
        for sym in metas - set(st.positions):
            self.db.execute("DELETE FROM position_meta WHERE account_id=? AND symbol=?", (self.account_id, sym))
        self._update_risk_state(account_uncertain=1 if problems else 0)
        for p in problems:
            self.alerts.raise_("critical", "reconciliation", f"Account discrepancy: {p}")
        return problems

    def _position_view(self, sym: str, p) -> PositionView | None:
        meta = self.db.one("SELECT * FROM position_meta WHERE account_id=? AND symbol=?", (self.account_id, sym))
        if meta is None:
            return None
        return PositionView(sym, date.fromisoformat(meta["entry_date"]), p.avg_price, meta["current_stop"] or 0,
                            meta["highest_close"] or p.avg_price, meta["bars_held"], meta["target"])

    def decide(self, day: date, allow_entries: bool = True, order_date: date | None = None) -> DayResult:
        """``order_date``: orders only become eligible at sessions AFTER this date.
        Normally the decision day; the paper runner passes today's date when
        today's session has already opened, so no order can fill at a price
        from the past."""
        res = DayResult(day)
        od = (order_date or day).isoformat()
        self.reconcile()
        blocks = self.global_blocks(day)
        if not allow_entries:
            blocks.append("New entries not allowed for this session (catch-up processing)")
        res.blocks = blocks
        state = self.broker.account_state()
        rs = self.risk_state()

        # ---- exits & trailing stops (allowed even when entries are blocked,
        #      because they reduce risk - but never if the account is uncertain)
        if not rs.get("account_uncertain"):
            for sym, p in state.positions.items():
                pv = self._position_view(sym, p)
                meta = self.db.one("SELECT strategy FROM position_meta WHERE account_id=? AND symbol=?", (self.account_id, sym))
                st = self.strategies.get(meta["strategy"]) if meta else None
                ind = self.ind.get((st.key, sym)) if st else None
                if pv is None or st is None or ind is None:
                    continue
                ex = st.exit(ind, day, pv)
                if ex is not None:
                    if not any(o["purpose"] in ("exit", "flatten") for o in self.broker.open_orders(sym)):
                        o = self.broker.submit_order(OrderRequest(
                            client_order_id=f"{self.account_id}:{sym}:exit:{day.isoformat()}", symbol=sym, side="SELL",
                            order_type="MARKET", quantity=p.quantity, purpose="exit", created_for_date=od,
                            strategy=st.key, reason=ex.reason))
                        res.orders.append(o)
                        self.alerts.raise_("info", "exit_signal",
                                           f"[PAPER] Exit order PLACED (not yet filled) for {sym}: {ex.reason}")
                    continue
                new_stop = st.trail_stop(ind, day, pv)
                if new_stop is not None and new_stop > pv.current_stop:
                    self._replace_stop(sym, p.quantity, new_stop, day, st.key, od)

        # ---- entries
        regime_ok = self.regime.risk_on(day)
        candidates = []
        for (key, sym), ind in self.ind.items():
            ins = self.data.instrument(sym)
            if not ins.get("enabled", True):
                continue
            sig = self.strategies[key].entry(sym, ind, day, regime_ok)
            if sig is not None:
                candidates.append(sig)
        order_keys = list(self.strategies)
        candidates.sort(key=lambda s: (order_keys.index(s.strategy), -s.rank_score))

        state = self.broker.account_state()
        pos_val, _, _ = self.valuation(state, day)
        equity = state.cash_gbp + pos_val
        held = [HeldItem(sym, p.quantity * (self.data.last_close(sym, day) or (p.avg_price, day))[0]
                         * (self.data.fx_to_gbp(self.data.currency(sym), day) or 1.0),
                         self.data.instrument(sym).get("sector", "Unknown")) for sym, p in state.positions.items()]
        pending = [HeldItem(o["symbol"], o["reserved_cash"], self.data.instrument(o["symbol"]).get("sector", "Unknown"))
                   for o in self.broker.open_orders() if o["side"] == "BUY"]
        reserved = state.reserved_gbp
        for sig in candidates:
            sym = sig.symbol
            ins = self.data.instrument(sym)
            ccy = self.data.currency(sym)
            fx = self.data.fx_to_gbp(ccy, day)
            h = self.data.history(sym)
            h = h[h.index <= day].tail(20)
            avg_vol = float(h["volume"].mean()) if len(h) else 0.0
            avg_val = float((h["volume"] * h["close"]).mean() * (fx or 0)) if len(h) else 0.0
            fresh, note = self.data.freshness(sym, day)
            ctx = RiskContext(self.mode if self.mode != "backtest" else "backtest", equity, state.cash_gbp, reserved,
                              held, pending, blocks, fresh, note,
                              self._correlated(sym, [x.symbol for x in held + pending], day))
            prop = Proposal(sym, sig.strategy, "BUY", sig.entry_est, sig.stop_distance, ccy, fx, ins,
                            avg_vol, avg_val, ins.get("sector", "Unknown"))
            dec = self.risk.evaluate(prop, ctx)
            sig_rec = {**sig.to_dict(), "risk": dec.to_dict(), "status": "approved" if dec.approved else "rejected"}
            if dec.approved:
                o = self.broker.submit_order(OrderRequest(
                    client_order_id=f"{self.account_id}:{sym}:entry:{day.isoformat()}", symbol=sym, side="BUY",
                    order_type="MARKET", quantity=dec.quantity, purpose="entry", created_for_date=od,
                    strategy=sig.strategy, reason="; ".join(sig.reasons), est_price=sig.entry_est,
                    reserved_cash=dec.reserved_gbp, stop_distance=sig.stop_distance))
                sig_rec["status"] = "ordered"
                sig_rec["order_id"] = o["id"]
                res.orders.append(o)
                pending.append(HeldItem(sym, dec.reserved_gbp, ins.get("sector", "Unknown")))
                reserved += dec.reserved_gbp
                self.alerts.raise_("info", "signal",
                                   f"[PAPER] New {sig.strategy} signal for {sym}: BUY order for {dec.quantity} shares "
                                   f"PLACED for the next open (proposed, not yet filled)", dedupe_hours=0)
            res.signals.append(sig_rec)
            if self.record_signals:
                self.db.execute("""INSERT INTO signals(account_id,as_of_date,symbol,strategy,action,entry_est,stop,target,
                                       rank_score,reasons,risks,risk_decision,status,order_id,created_at)
                                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                (self.account_id, day.isoformat(), sym, sig.strategy, "BUY", sig.entry_est, sig.stop_est,
                                 sig.target, sig.rank_score, json.dumps(sig.reasons), json.dumps(sig.risks),
                                 json.dumps(dec.to_dict()), sig_rec["status"], sig_rec.get("order_id"), utcnow()))
        return res

    def _replace_stop(self, sym: str, qty: float, new_stop: float, day: date, strategy: str, od: str) -> None:
        old = [o for o in self.broker.open_orders(sym) if o["order_type"] == "STOP"]
        new = self.broker.submit_order(OrderRequest(
            client_order_id=f"{self.account_id}:{sym}:stop:{day.isoformat()}:trail", symbol=sym, side="SELL",
            order_type="STOP", quantity=qty, stop_price=round(new_stop, 6), purpose="stop",
            created_for_date=od, strategy=strategy, reason=f"Trailing stop raised to {new_stop:.4f}"))
        for o in old:
            if o["id"] != new["id"]:
                self.broker.cancel_order(o["id"], f"replaced by trailing stop {new_stop:.4f}")
        self.db.execute("UPDATE position_meta SET current_stop=? WHERE account_id=? AND symbol=?",
                        (new_stop, self.account_id, sym))

    def _correlated(self, sym: str, others: list[str], day: date) -> list[str]:
        r = self.s["risk"]
        n = int(r["correlation_lookback_days"])
        base = self._returns.get(sym)
        if base is None:
            return []
        a = base[base.index <= day].tail(n)
        out = []
        for o in others:
            b = self._returns.get(o)
            if b is None:
                continue
            j = pd.concat([a, b[b.index <= day].tail(n)], axis=1, join="inner").dropna()
            if len(j) >= n // 2:
                c = float(np.corrcoef(j.iloc[:, 0], j.iloc[:, 1])[0, 1])
                if c > float(r["correlation_threshold"]):
                    out.append(o)
        return out

    # ------------------------------------------------------------ manual controls
    def set_kill_switch(self, on: bool) -> None:
        self._update_risk_state(kill_switch=1 if on else 0)
        if on:
            for o in self.broker.open_orders():
                if o["purpose"] == "entry":
                    self.broker.cancel_order(o["id"], "cancelled by EMERGENCY STOP")
            self.alerts.raise_("critical", "kill_switch", "EMERGENCY STOP activated: pending entry orders cancelled; "
                                                          "protective stops kept; no new orders until released.")
        else:
            self.alerts.raise_("warning", "kill_switch", "Emergency stop released by user.")

    def resume_from_pause(self) -> None:
        st = self.broker.account_state()
        last = self.db.one("SELECT equity_gbp FROM equity_snapshots WHERE account_id=? ORDER BY date DESC LIMIT 1",
                           (self.account_id,))
        eq = float(last[0]) if last else st.cash_gbp
        self._update_risk_state(paused_reason=None, peak_equity=eq, consecutive_failures=0)
        self.alerts.raise_("warning", "risk", f"Trading pause cleared by user; drawdown measured from £{eq:,.2f} again.")

    def flatten_all(self, day: date) -> list[dict]:
        out = []
        for sym, p in self.broker.account_state().positions.items():
            out.append(self.broker.submit_order(OrderRequest(
                client_order_id=f"{self.account_id}:{sym}:flatten:{day.isoformat()}", symbol=sym, side="SELL",
                order_type="MARKET", quantity=p.quantity, purpose="flatten", created_for_date=day.isoformat(),
                reason="User requested: close all positions")))
        return out
