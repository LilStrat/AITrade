"""Strategy 2 - Volume-confirmed breakout with trend/momentum filters."""

from __future__ import annotations

from datetime import date

import pandas as pd

from ..indicators import atr, highest, lowest, rate_of_change, sma
from .base import EntrySignal, ExitSignal, ParamSpec, PositionView, Strategy, any_nan, row_at


class BreakoutMomentum(Strategy):
    key = "breakout_momentum"
    name = "Breakout Momentum (new high on strong volume)"
    summary = ("Buys when an instrument with positive 6-month momentum closes at a new multi-week high on "
               "above-average volume, then rides the trend with a trailing stop. Typical hold: weeks.")
    documentation = {
        "behaviour": ("Trend following / time-series momentum: assets that are rising tend, on average, to keep "
                      "rising for a while (e.g. Moskowitz, Ooi & Pedersen 2012; Donchian channel breakouts). "
                      "Volume confirmation aims to filter breakouts with little participation."),
        "instruments": "Liquid LSE shares and ETFs. Daily bars. Holding from days to a few months (configurable).",
        "entry": ("All must be true at the close: (1) close above the highest close of the previous N days "
                  "(default 55); (2) volume at least 1.5x its previous 20-day average; (3) close above the "
                  "200-day SMA; (4) 126-day (about 6-month) return positive; (5) ATR(14) under the volatility "
                  "cap (default 6%); (6) close not more than 12% above its 20-day SMA (not over-extended); "
                  "(7) market regime risk-on. Buy at the next session's open."),
        "exit": ("Sell at the next open when the close falls below the lowest low of the previous 20 days, or "
                 "the position reaches the maximum holding period (default 60 sessions). The trailing stop "
                 "can trigger during a session."),
        "stops": ("Initial stop = fill price minus 2 x ATR(14). Afterwards a trailing 'chandelier' stop at the "
                  "highest close since entry minus 3 x ATR(14); it only ever moves up. Gaps below the stop fill "
                  "at the (worse) open. No fixed profit target: profits are taken by the trailing stop."),
        "invalidation": ("No trade in a risk-off market, when 6-month momentum is negative, when the breakout is on "
                         "low volume, or when the price is already far above its short-term average."),
        "risks": ("Breakouts often fail ('false breakouts'), giving many small losses; profits depend on a few "
                  "large trends. Trend-following performs badly in choppy, range-bound markets. Win rates are "
                  "typically below 50% by design."),
        "evidence": "See the Backtesting page and docs/STRATEGIES.md. Must be re-run on your real data.",
        "sensitivity": "Use the walk-forward research run to view results across breakout lengths and trailing-stop widths.",
    }
    param_specs = [
        ParamSpec("breakout_len", 55, 10, 250, "Breakout lookback (days)"),
        ParamSpec("volume_mult", 1.5, 1.0, 4.0, "Volume must be this multiple of the 20-day average", integer=False),
        ParamSpec("trend_sma", 200, 100, 300, "Long-term trend average (days)"),
        ParamSpec("momentum_len", 126, 20, 252, "Momentum lookback (days)"),
        ParamSpec("stop_atr", 2.0, 1.0, 5.0, "Initial stop distance in ATRs", integer=False),
        ParamSpec("trail_atr", 3.0, 1.5, 6.0, "Trailing stop distance in ATRs", integer=False),
        ParamSpec("exit_low_len", 20, 5, 100, "Exit on a close below this-many-day low"),
        ParamSpec("max_hold", 60, 5, 250, "Maximum holding period (sessions)"),
        ParamSpec("max_atr_pct", 6, 1, 15, "Skip if ATR is above this % of price", integer=False),
        ParamSpec("max_extension_pct", 12, 2, 40, "Skip if close is more than this % above its 20-day SMA", integer=False),
    ]

    @property
    def warmup_bars(self) -> int:
        return int(max(self.params["trend_sma"], self.params["momentum_len"], self.params["breakout_len"])) + 5

    def prepare(self, bars: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        df = bars[["open", "high", "low", "close", "volume"]].copy()
        # shift(1): compare today's close with the PREVIOUS N days only.
        df["prior_high"] = highest(df["close"], int(p["breakout_len"])).shift(1)
        df["prior_low"] = lowest(df["low"], int(p["exit_low_len"])).shift(1)
        df["vol_avg"] = sma(df["volume"], 20).shift(1)
        df["sma_trend"] = sma(df["close"], int(p["trend_sma"]))
        df["sma20"] = sma(df["close"], 20)
        df["mom"] = rate_of_change(df["close"], int(p["momentum_len"]))
        df["atr"] = atr(df["high"], df["low"], df["close"], 14)
        return df

    def entry(self, symbol: str, ind: pd.DataFrame, day: date, regime_ok: bool) -> EntrySignal | None:
        r = row_at(ind, day)
        if r is None or any_nan(r, ("prior_high", "vol_avg", "sma_trend", "sma20", "mom", "atr")):
            return None
        p = self.params
        if r["vol_avg"] <= 0:
            return None
        atr_pct = r["atr"] / r["close"] * 100
        ext = (r["close"] / r["sma20"] - 1) * 100
        vol_ratio = r["volume"] / r["vol_avg"]
        conds = [
            (r["close"] > r["prior_high"], f"Close {r['close']:.2f} above prior {p['breakout_len']}-day high {r['prior_high']:.2f}"),
            (vol_ratio >= p["volume_mult"], f"Volume {vol_ratio:.1f}x its 20-day average (needs {p['volume_mult']}x)"),
            (r["close"] > r["sma_trend"], f"Close above {p['trend_sma']}-day SMA {r['sma_trend']:.2f}"),
            (r["mom"] > 0, f"{p['momentum_len']}-day return {r['mom'] * 100:+.1f}%"),
            (atr_pct < p["max_atr_pct"], f"ATR(14) = {atr_pct:.2f}% of price (below {p['max_atr_pct']}%)"),
            (ext <= p["max_extension_pct"], f"{ext:+.1f}% vs 20-day SMA (limit {p['max_extension_pct']}%)"),
            (regime_ok, "Market regime risk-on"),
        ]
        if not all(c for c, _ in conds):
            return None
        return EntrySignal(
            symbol=symbol, strategy=self.key, as_of=day, entry_est=float(r["close"]),
            stop_distance=float(p["stop_atr"] * r["atr"]), target=None,
            rank_score=float(r["mom"] * 100),
            reasons=[t for _, t in conds],
            risks=["Breakouts frequently fail and reverse.",
                   "Results rely on a minority of large winning trends.",
                   "Overnight gaps can exceed the stop."])

    def exit(self, ind: pd.DataFrame, day: date, pos: PositionView) -> ExitSignal | None:
        r = row_at(ind, day)
        if r is None:
            return None
        p = self.params
        if not any_nan(r, ("prior_low",)) and r["close"] < r["prior_low"]:
            return ExitSignal(pos.symbol, f"Close {r['close']:.2f} below the prior {p['exit_low_len']}-day low {r['prior_low']:.2f}")
        if pos.bars_held >= p["max_hold"]:
            return ExitSignal(pos.symbol, f"Time stop: held {pos.bars_held} sessions (max {p['max_hold']})")
        return None

    def trail_stop(self, ind: pd.DataFrame, day: date, pos: PositionView) -> float | None:
        r = row_at(ind, day)
        if r is None or any_nan(r, ("atr",)):
            return None
        level = pos.highest_close - self.params["trail_atr"] * float(r["atr"])
        return level if level > pos.current_stop else None
