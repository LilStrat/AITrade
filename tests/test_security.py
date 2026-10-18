import logging
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SOURCE = [p for p in REPO.rglob("*") if p.suffix in (".py", ".yaml", ".html", ".bat", ".md", ".txt", ".example")
          and ".venv" not in p.parts and "tests" not in p.parts and ".git" not in p.parts]


def test_no_hardcoded_secrets_in_source():
    pat = re.compile(r"(api[_-]?token|api[_-]?key|password|secret)\s*[:=]\s*['\"][A-Za-z0-9_\-]{12,}['\"]", re.I)
    hits = [f"{p}: {m.group(0)}" for p in SOURCE for m in pat.finditer(p.read_text(encoding="utf-8", errors="ignore"))]
    assert not hits, hits


def test_gitignore_protects_private_files():
    gi = (REPO / ".gitignore").read_text()
    for entry in (".env", "database/", "logs/", "backups/", "exports/", "config/settings.yaml"):
        assert entry in gi


def test_dashboard_binds_to_localhost_only():
    src = (REPO / "app.py").read_text()
    assert 'host="127.0.0.1"' in src and "0.0.0.0" not in src


def test_log_redaction(monkeypatch, tmp_path):
    from tradebot import config
    from tradebot.logging_setup import RedactingFilter, redact
    monkeypatch.setenv("EODHD_API_TOKEN", "supersecrettoken123456")
    assert "supersecret" not in redact("GET https://eodhd.com/api/eod/VOD.LSE?api_token=supersecrettoken123456&fmt=json")
    assert "hunter2pass" not in redact("password=hunter2pass")
    rec = logging.LogRecord("x", logging.INFO, "f", 1, "token used: %s", ("supersecrettoken123456",), None)
    RedactingFilter().filter(rec)
    assert "supersecret" not in rec.getMessage()


def test_dashboard_security(ctx):
    from fastapi.testclient import TestClient
    from tradebot.dashboard.server import create_app
    app = create_app(ctx)
    c = TestClient(app, base_url="http://127.0.0.1")
    assert c.get("/").status_code == 200
    # DNS-rebinding / external host header is refused
    evil = TestClient(app, base_url="http://attacker.example")
    assert evil.get("/").status_code == 400
    # state-changing request without the CSRF token is refused
    r = c.post("/action/kill", data={"csrf_token": "wrong", "on": "1"})
    assert r.status_code == 403
    assert not ctx.db.one("SELECT kill_switch FROM risk_state")[0]
    # downloads cannot escape the exports folder
    assert c.get("/download/..%2F..%2Fconfig/default_settings.yaml").status_code in (404, 400)
    assert c.get("/download/../database/tradebot.sqlite3").status_code in (404, 400)
    # shutdown needs the local control token
    assert c.post("/action/shutdown", data={"token": "guess"}).status_code == 403
    # no API docs / schema exposed
    assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404


def test_live_mode_cannot_be_selected_from_dashboard(ctx):
    from fastapi.testclient import TestClient
    from tradebot.dashboard.server import create_app
    app = create_app(ctx)
    c = TestClient(app, base_url="http://127.0.0.1", follow_redirects=False)
    r = c.post("/action/mode", data={"csrf_token": app.state.csrf, "mode": "live", "confirm": "PAPER"})
    assert r.status_code == 303 and ctx.settings["app"]["mode"] == "local_paper"


def test_env_example_has_no_values():
    for line in (REPO / ".env.example").read_text().splitlines():
        if line and not line.startswith("#"):
            key, _, val = line.partition("=")
            assert val.strip() == "", f"{key} has a value in .env.example"
