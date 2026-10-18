"""Strategy 1 - Short-term pullback within an established uptrend."""

from __future__ import annotations

from datetime import date

import pandas as pd

from ..indicators import atr, rate_of_change, rsi, sma
from .base import EntrySignal, ExitSignal, ParamSpec, PositionView, Strategy, any_nan, row_at


class TrendPullback(Strategy):
    key = "trend_pullback"
    name = "Trend Pullback (RSI-2 dip in an uptrend)"
    summary = ("Buys a short, sharp dip in an instrument that is in a long-term uptrend, and sells "
               "once the price recovers above its short-term average. Typical hold: 1-10 sessions.")
    documentation = {
        "behaviour": ("Short-term mean reversion: liquid shares and broad ETFs in long-term uptrends "
                      "have historically tended to bounce after a few sharply down days. Published "
                      "research (e.g. Connors & Alvarez, 2008) found this mostly on US indices; the "
                      "effect has weakened over time and must be re-tested on UK data."),
        "instruments": "Liquid LSE large caps and broad-market ETFs. Daily bars. Holding 1-10 sessions.",
        "entry": ("All must be true at the close: (1) close above its 200-day SMA; (2) 50-day SMA above "
                  "200-day SMA; (3) 2-day RSI below the entry threshold (default 10); (4) 5-day fall no "
                  "worse than the crash limit (default -12%); (5) ATR(14) under the volatility cap "
                  "(default 5% of price); (6) market regime risk-on. Buy at the next session's open."),
        "exit": ("Sell at the next open when ANY is true at a close: close above the 5-day SMA (bounce "
                 "achieved); close below the 200-day SMA (trend broken); position held for the maximum "
                 "number of sessions (default 10). The protective stop can also trigger during a session."),
        "stops": ("Initial stop = fill price minus 2.5 x ATR(14). It is a resting stop, checked against each "
                  "day's low. If the price opens below the stop, the simulated fill is at that (worse) open. "
                  "Profit-taking is the 5-day SMA rule; there is no fixed target."),
        "invalidation": ("No trade when the long-term trend is down, the market regime is risk-off, the fall "
                         "is crash-like (often news-driven), or volatility is extreme."),
        "risks": ("Buying weakness can mean catching a falling knife: company-specific bad news can keep "
                  "pushing the price down and overnight gaps can exceed the stop. Many small wins can be "
                  "wiped out by a few large losses. Frequent trading makes UK stamp duty (0.5% on shares) "
                  "and commissions a large drag - ETFs avoid stamp duty."),
        "evidence": "See the Backtesting page and docs/STRATEGIES.md. Must be re-run on your real data.",
        "sensitivity": "Use the walk-forward research run to view results across a grid of RSI thresholds and stop widths.",
    }
    param_specs = [
        ParamSpec("trend_sma", 200, 100, 300, "Long-term trend average (days)"),
        ParamSpec("mid_sma", 50, 20, 100, "Medium-term average that must be above the trend average (days)"),
        ParamSpec("rsi_len", 2, 2, 14, "RSI length (days)"),
        ParamSpec("rsi_entry", 10, 2, 40, "Enter when RSI is below this", integer=False),
        ParamSpec("exit_sma", 5, 3, 20, "Exit when the close is above this average (days)"),
        ParamSpec("stop_atr", 2.5, 1.0, 5.0, "Stop distance in ATRs", integer=False),
        ParamSpec("max_hold", 10, 2, 40, "Maximum holding period (sessions)"),
        ParamSpec("crash_limit_pct", 12, 3, 30, "Skip if the 5-day fall is worse than this (%)", integer=False),
        ParamSpec("max_atr_pct", 5, 1, 15, "Skip if ATR is above this % of price", integer=False),
    ]

    @property
    def warmup_bars(self) -> int:
        return int(self.params["trend_sma"]) + 5

    def prepare(self, bars: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        df = bars[["open", "high", "low", "close", "volume"]].copy()
        df["sma_trend"] = sma(df["close"], int(p["trend_sma"]))
        df["sma_mid"] = sma(df["close"], int(p["mid_sma"]))
        df["sma_exit"] = sma(df["close"], int(p["exit_sma"]))
        df["rsi"] = rsi(df["close"], int(p["rsi_len"]))
        df["atr"] = atr(df["high"], df["low"], df["close"], 14)
        df["ret5"] = rate_of_change(df["close"], 5)
        return df

    def entry(self, symbol: str, ind: pd.DataFrame, day: date, regime_ok: bool) -> EntrySignal | None:
        r = row_at(ind, day)
        if r is None or any_nan(r, ("sma_trend", "sma_mid", "rsi", "atr", "ret5")):
            return None
        p = self.params
        atr_pct = r["atr"] / r["close"] * 100
        conds = [
            (r["close"] > r["sma_trend"], f"Close {r['close']:.2f} above {p['trend_sma']}-day SMA {r['sma_trend']:.2f}"),
            (r["sma_mid"] > r["sma_trend"], f"{p['mid_sma']}-day SMA {r['sma_mid']:.2f} above {p['trend_sma']}-day SMA"),
            (r["rsi"] < p["rsi_entry"], f"RSI({p['rsi_len']}) = {r['rsi']:.1f}, below {p['rsi_entry']}"),
            (r["ret5"] * 100 > -p["crash_limit_pct"], f"5-day change {r['ret5'] * 100:+.1f}% (not crash-like)"),
            (atr_pct < p["max_atr_pct"], f"ATR(14) = {atr_pct:.2f}% of price (below {p['max_atr_pct']}%)"),
            (regime_ok, "Market regime risk-on"),
        ]
        if not all(c for c, _ in conds):
            return None
        risks = [
            "A dip can continue: news-driven falls often keep going.",
            f"Overnight gaps can exceed the stop ({p['stop_atr']} x ATR below entry).",
        ]
        if r["ret5"] < -0.06:
            risks.append(f"Fall over 5 days is already {r['ret5'] * 100:.1f}% - check for company news.")
        return EntrySignal(
            symbol=symbol, strategy=self.key, as_of=day, entry_est=float(r["close"]),
            stop_distance=float(p["stop_atr"] * r["atr"]), target=None,
            rank_score=float(p["rsi_entry"] - r["rsi"]),
            reasons=[t for _, t in conds], risks=risks)

    def exit(self, ind: pd.DataFrame, day: date, pos: PositionView) -> ExitSignal | None:
        r = row_at(ind, day)
        if r is None:
            return None
        p = self.params
        if not any_nan(r, ("sma_exit",)) and r["close"] > r["sma_exit"]:
            return ExitSignal(pos.symbol, f"Close {r['close']:.2f} above {p['exit_sma']}-day SMA {r['sma_exit']:.2f} (bounce achieved)")
        if not any_nan(r, ("sma_trend",)) and r["close"] < r["sma_trend"]:
            return ExitSignal(pos.symbol, f"Close fell below the {p['trend_sma']}-day SMA (trend broken)")
        if pos.bars_held >= p["max_hold"]:
            return ExitSignal(pos.symbol, f"Time stop: held {pos.bars_held} sessions (max {p['max_hold']})")
        return None
