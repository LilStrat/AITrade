"""AI-assisted explanations. INTERPRETATION ONLY.

Design rules (enforced by code and tests):
* This module never imports brokers, the trading core or the risk manager,
  and returns plain text only. It cannot create, change or cancel orders.
* The model receives a JSON block of verified facts computed by
  deterministic code and is told to use only those facts.
* The output is stored and displayed separately, labelled as AI-generated.
* Numbers in the output that do not appear in the facts are flagged.
* If the model is unavailable, callers carry on without it.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from ..db import Database
from .ollama_client import OllamaClient, OllamaUnavailable

SYSTEM = (
    "You are a cautious assistant explaining a trading tool's output to a beginner in the UK. "
    "Rules: (1) Use ONLY the facts in the JSON provided; never invent prices, figures or news. "
    "(2) Do not tell the user to buy or sell, and do not predict prices. "
    "(3) Clearly separate: FACTS (restated from the JSON), INTERPRETATION (your reading), and "
    "UNCERTAINTIES / CONTRADICTORY EVIDENCE (reasons the setup could fail). "
    "(4) Never describe a signal or score as a probability of profit. "
    "(5) Be concise: under 250 words. Use plain English and British spelling."
)

LABEL = "AI-generated interpretation (local Llama model). It may be wrong and does not affect any trading decision."


def _numbers(text: str) -> set[str]:
    return {m.replace(",", "") for m in re.findall(r"-?\d[\d,]*\.?\d*", text)}


def ungrounded_numbers(output: str, facts: dict) -> list[str]:
    """Numbers in the output that cannot be found (approximately) in the facts."""
    fact_text = json.dumps(facts)
    fact_vals = []
    for n in _numbers(fact_text):
        try:
            fact_vals.append(float(n))
        except ValueError:
            pass
    bad = []
    for n in _numbers(output):
        try:
            v = float(n)
        except ValueError:
            continue
        if abs(v) < 10 and float(v).is_integer():
            continue  # small counts like "2 reasons" / list numbering
        if not any(abs(v - f) <= max(0.051, abs(f) * 0.006) for f in fact_vals):
            bad.append(n)
    return sorted(set(bad))


class Analyst:
    def __init__(self, db: Database, settings: dict, client: OllamaClient | None = None):
        self.db = db
        self.enabled = bool(settings["ai"].get("enabled", True))
        self.client = client or OllamaClient(settings)

    def status(self) -> dict:
        if not self.enabled:
            return {"available": False, "disabled": True}
        return self.client.status()

    def _store(self, kind: str, subject: str, facts: dict, output: str | None, status: str) -> int:
        cur = self.db.execute("INSERT INTO ai_notes(ts,kind,subject,facts,output,model,status) VALUES (?,?,?,?,?,?,?)",
                              (datetime.now(timezone.utc).isoformat(timespec="seconds"), kind, subject,
                               json.dumps(facts, default=str), output, self.client.model, status))
        return int(cur.lastrowid)

    def ask(self, kind: str, subject: str, facts: dict, question: str) -> dict:
        if not self.enabled:
            return {"ok": False, "label": LABEL, "text": None, "message": "AI assistance is switched off in Settings."}
        prompt = f"FACTS (verified, JSON):\n{json.dumps(facts, default=str, indent=1)}\n\nTASK: {question}"
        try:
            text = self.client.generate(SYSTEM, prompt)
        except OllamaUnavailable as exc:
            self._store(kind, subject, facts, None, "unavailable")
            return {"ok": False, "label": LABEL, "text": None,
                    "message": f"{exc}. The trading system works normally without it."}
        flags = ungrounded_numbers(text, facts)
        status = "ok" if not flags else "ok-with-warnings"
        note_id = self._store(kind, subject, facts, text, status)
        return {"ok": True, "id": note_id, "label": LABEL, "text": text,
                "warnings": ([f"The AI text contains figures not found in the verified data: {', '.join(flags[:8])}. "
                              "Treat them as unreliable."] if flags else [])}

    # ---- specific tasks --------------------------------------------------
    def explain_signal(self, signal: dict) -> dict:
        facts = {
            "symbol": signal["symbol"], "strategy": signal["strategy"], "date": signal["as_of_date"],
            "estimated_entry": signal["entry_est"], "proposed_stop": signal["stop"],
            "conditions_met": json.loads(signal["reasons"] or "[]"),
            "known_risks": json.loads(signal["risks"] or "[]"),
            "risk_manager": {k: v for k, v in json.loads(signal["risk_decision"] or "{}").items()
                             if k in ("approved", "quantity", "rejections", "warnings", "risk_gbp", "value_gbp")},
            "status": signal["status"],
        }
        return self.ask("signal", f"{signal['symbol']} {signal['as_of_date']}", facts,
                        "Explain in plain English why this signal qualified under the strategy rules, what the "
                        "risk manager decided and why, and list the main reasons this trade could fail.")

    def market_summary(self, facts: dict) -> dict:
        return self.ask("market", facts.get("date", ""), facts,
                        "Summarise current market conditions for the watchlist using only these facts. "
                        "Mention breadth, trend and any data-quality warnings. Note what is unknown.")

    def report_commentary(self, facts: dict) -> dict:
        return self.ask("report", facts.get("period", ""), facts,
                        "Write a short commentary on this paper-trading period: what happened, what the costs "
                        "were, how it compares with the benchmark, and what should be investigated. Do not "
                        "speculate about future performance.")

    def recent(self, limit: int = 30) -> list[dict]:
        return [dict(r) for r in self.db.query("SELECT * FROM ai_notes ORDER BY id DESC LIMIT ?", (limit,))]
