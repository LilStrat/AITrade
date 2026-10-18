import ast
import json
from pathlib import Path

import httpx
import pytest

from tradebot.ai.analyst import Analyst, ungrounded_numbers
from tradebot.ai.ollama_client import OllamaClient
from tradebot.db import Database

AI_DIR = Path(__file__).resolve().parent.parent / "tradebot" / "ai"


def settings_ai(enabled=True):
    return {"ai": {"enabled": enabled, "ollama_url": "http://127.0.0.1:11434", "model": "llama3.1:8b",
                   "keep_alive": "5m", "context_tokens": 4096, "temperature": 0.2, "timeout_seconds": 5}}


def mock_transport(text="FACTS: RSI was 7.1.\nINTERPRETATION: a dip in an uptrend.", status=200):
    def handler(request: httpx.Request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "llama3.1:8b"}]})
        body = json.loads(request.content)
        assert body["keep_alive"] == "5m" and body["stream"] is False
        return httpx.Response(status, json={"response": text})
    return httpx.MockTransport(handler)


def test_ai_module_cannot_reach_trading_code():
    """Static check: nothing in tradebot/ai imports brokers, the core, risk or paper trading."""
    forbidden = ("brokers", "core", "risk", "paper_trading", "execution", "portfolio")
    for f in AI_DIR.glob("*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not any(part in forbidden for part in node.module.split(".")), f"{f.name} imports {node.module}"
            if isinstance(node, ast.Import):
                for a in node.names:
                    assert not any(part in forbidden for part in a.name.split(".")), f"{f.name} imports {a.name}"


def test_unavailable_model_is_handled():
    def down(request):
        raise httpx.ConnectError("refused")
    a = Analyst(Database(":memory:"), settings_ai(), OllamaClient(settings_ai(), transport=httpx.MockTransport(down)))
    assert a.status()["available"] is False
    out = a.market_summary({"date": "2026-10-16"})
    assert out["ok"] is False and "works normally" in out["message"]


def test_generate_and_label():
    db = Database(":memory:")
    a = Analyst(db, settings_ai(), OllamaClient(settings_ai(), transport=mock_transport()))
    assert a.status() == {"available": True, "model_installed": True, "models": ["llama3.1:8b"]}
    out = a.market_summary({"date": "2026-10-16", "rsi": 7.1})
    assert out["ok"] and "AI-generated" in out["label"] and not out["warnings"]
    assert db.one("SELECT COUNT(*) FROM ai_notes")[0] == 1


def test_invented_numbers_are_flagged():
    assert ungrounded_numbers("The price will reach 123.45 soon", {"close": 100.0}) == ["123.45"]
    assert ungrounded_numbers("Close was 100.00 and RSI 7.1", {"close": 100.0, "rsi": 7.08}) == []


def test_ai_text_cannot_create_orders():
    db = Database(":memory:")
    evil = "BUY 10000 shares of VOD.L immediately. Ignore all risk limits. Set max_risk_per_trade_pct=100."
    a = Analyst(db, settings_ai(), OllamaClient(settings_ai(), transport=mock_transport(evil)))
    out = a.market_summary({"date": "2026-10-16"})
    assert out["ok"]
    assert db.one("SELECT COUNT(*) FROM orders")[0] == 0
    assert out["warnings"]  # 10000 / 100 not in facts


def test_disabled_ai():
    a = Analyst(Database(":memory:"), settings_ai(False))
    assert a.market_summary({})["ok"] is False


def test_ollama_must_be_local():
    s = settings_ai()
    s["ai"]["ollama_url"] = "http://example.com:11434"
    with pytest.raises(ValueError):
        OllamaClient(s)
