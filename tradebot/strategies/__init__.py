"""Strategy registry."""

from __future__ import annotations

from .base import Strategy
from .breakout_momentum import BreakoutMomentum
from .trend_pullback import TrendPullback

REGISTRY: dict[str, type[Strategy]] = {
    TrendPullback.key: TrendPullback,
    BreakoutMomentum.key: BreakoutMomentum,
}


def build_strategies(settings: dict, only_enabled: bool = True) -> list[Strategy]:
    out = []
    conf = settings.get("strategies", {})
    for key, cls in REGISTRY.items():
        c = conf.get(key, {}) or {}
        if only_enabled and not c.get("enabled", False):
            continue
        out.append(cls(c.get("params") or {}))
    return out
