"""Hard guard against accidental real-money trading.

v1 contains NO live broker adapter. Even so, this guard exists so that a
future adapter cannot be used unless every condition below is met. There is
no automatic fallback from paper to live in any code path.
"""

from __future__ import annotations

import os

LIVE_PHRASE = "I UNDERSTAND THIS USES REAL MONEY"


class LiveTradingNotAuthorised(PermissionError):
    pass


def order_capable_mode(settings: dict) -> str:
    """Return the mode if it may place (simulated) orders, else raise."""
    mode = settings["app"]["mode"]
    if mode == "local_paper":
        return mode
    if mode == "broker_paper":
        raise LiveTradingNotAuthorised(
            "Broker paper trading is not available in this version. Switch back to local_paper.")
    if mode == "live":
        problems = live_readiness_problems(settings)
        raise LiveTradingNotAuthorised(
            "Live trading is disabled. " + ("; ".join(problems) if problems else
                                            "No live broker adapter is installed in this version."))
    raise LiveTradingNotAuthorised(f"unknown mode {mode!r}")


def live_readiness_problems(settings: dict) -> list[str]:
    problems = ["No live broker adapter exists in v1 (see docs/LIVE_TRADING.md)"]
    if os.environ.get("TRADEBOT_LIVE_AUTHORISED") != LIVE_PHRASE:
        problems.append("TRADEBOT_LIVE_AUTHORISED confirmation phrase not set in .env")
    return problems
