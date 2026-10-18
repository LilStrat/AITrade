"""Performance metrics. All figures are computed from simulated data and
describe the past; they are not forecasts."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def max_drawdown(equity: pd.Series) -> tuple[float, int]:
    """(max drawdown as a positive %, longest drawdown duration in sessions)"""
    if equity.empty:
        return 0.0, 0
    peak = equity.cummax()
    dd = (peak - equity) / peak
    longest = cur = 0
    for v in dd.to_numpy():
        cur = cur + 1 if v > 1e-12 else 0
        longest = max(longest, cur)
    return float(dd.max() * 100), longest


def equity_metrics(equity: pd.Series, cash_rate_pct: float = 0.0) -> dict:
    if len(equity) < 2:
        return {"total_return_pct": 0.0}
    rets = equity.pct_change().dropna()
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1e-9)
    total = equity.iloc[-1] / equity.iloc[0] - 1
    rf_daily = (1 + cash_rate_pct / 100) ** (1 / TRADING_DAYS) - 1
    excess = rets - rf_daily
    vol = float(rets.std(ddof=1) * math.sqrt(TRADING_DAYS)) if len(rets) > 1 else 0.0
    downside = rets[rets < 0]
    dvol = float(math.sqrt((downside ** 2).sum() / len(rets)) * math.sqrt(TRADING_DAYS)) if len(rets) else 0.0
    sharpe = float(excess.mean() / rets.std(ddof=1) * math.sqrt(TRADING_DAYS)) if rets.std(ddof=1) > 0 else 0.0
    sortino = float(excess.mean() * TRADING_DAYS / dvol) if dvol > 0 else 0.0
    mdd, mdd_len = max_drawdown(equity)
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1 if years >= 1 else None
    return {
        "start_equity": float(equity.iloc[0]),
        "end_equity": float(equity.iloc[-1]),
        "total_return_pct": float(total * 100),
        "annualised_return_pct": float(cagr * 100) if cagr is not None else None,
        "years": float(years),
        "volatility_pct": vol * 100,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown_pct": mdd,
        "max_drawdown_sessions": mdd_len,
        "calmar": float(cagr * 100 / mdd) if cagr is not None and mdd > 0 else None,
        "worst_day_pct": float(rets.min() * 100),
        "best_day_pct": float(rets.max() * 100),
    }


def trade_metrics(trades: list[dict]) -> dict:
    closed = [t for t in trades if t.get("exit_date")]
    if not closed:
        return {"trades": 0, "open_trades": len(trades) - len(closed)}
    net = np.array([t["net_pnl_gbp"] for t in closed])
    gross = np.array([t["gross_pnl_gbp"] for t in closed])
    wins, losses = net[net > 0], net[net <= 0]
    pf = float(wins.sum() / -losses.sum()) if losses.sum() < 0 else (math.inf if wins.sum() > 0 else 0.0)
    return {
        "trades": len(closed),
        "open_trades": len(trades) - len(closed),
        "win_rate_pct": float(len(wins) / len(closed) * 100),
        "avg_win_gbp": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss_gbp": float(losses.mean()) if len(losses) else 0.0,
        "largest_loss_gbp": float(net.min()),
        "largest_win_gbp": float(net.max()),
        "profit_factor": pf,
        "expectancy_gbp": float(net.mean()),
        "net_pnl_gbp": float(net.sum()),
        "gross_pnl_before_costs_gbp": float(gross.sum()),
        "total_trade_costs_gbp": float(sum(t["costs_gbp"] for t in closed)),
        "avg_holding_sessions": float(np.mean([t["holding_sessions"] for t in closed])),
        "stopped_out_pct": float(sum(1 for t in closed if t["exit_purpose"] == "stop") / len(closed) * 100),
        "gap_stop_fills": int(sum(1 for t in closed if "GAP" in (t.get("exit_note") or ""))),
    }


def exposure_pct(positions_value: pd.Series, equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    return float((positions_value / equity).mean() * 100)
