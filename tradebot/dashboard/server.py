"""Local web dashboard (FastAPI). Listens on 127.0.0.1 only.

Security: Host-header allow-list (blocks DNS-rebinding), per-process CSRF
token on every state-changing request, no stack traces or secrets in pages,
downloads restricted to the exports folder.
"""

from __future__ import annotations

import copy
import json
import logging
import secrets
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .. import __version__, paths
from ..backtesting.data import load_view, synthetic_view
from ..backtesting.engine import BacktestRequest, run_backtest
from ..backtesting.research import research_markdown, run_research
from ..backup import create_backup
from ..config import ConfigError, load_universe, save_user_settings
from ..data import calendar as cal
from ..reports.export import export_account
from ..reports.generator import generate
from ..strategies import REGISTRY
from . import views
from .charts import line_chart

log = logging.getLogger(__name__)
HERE = Path(__file__).parent

# Editable settings: (section, key, label, help, kind, protected)
SETTINGS_FIELDS = [
    ("risk", "max_risk_per_trade_pct", "Max risk per trade (% of equity)",
     "How much of your equity you lose if a trade's stop is hit (before gaps/slippage). Higher = bigger positions and bigger losses.", "float", True),
    ("risk", "max_daily_loss_pct", "Daily loss limit (%)",
     "If equity falls this much in one day, no new entries are made that day.", "float", True),
    ("risk", "max_drawdown_pct", "Drawdown pause (%)",
     "If equity falls this far below its peak, ALL new entries stop until you review and press Resume.", "float", True),
    ("risk", "max_position_pct", "Max single position (% of equity)", "Caps concentration in any one instrument.", "float", True),
    ("risk", "max_total_exposure_pct", "Max total invested (% of equity)", "Cannot exceed 100% (no borrowing).", "float", True),
    ("risk", "max_sector_pct", "Max per sector (% of equity)", "Limits exposure to one sector.", "float", True),
    ("risk", "max_open_positions", "Max open positions", "Maximum number of holdings at once.", "int", True),
    ("risk", "max_correlated_positions", "Max highly-correlated holdings", "Avoids several bets on the same move.", "int", True),
    ("risk", "min_cash_reserve_pct", "Cash always kept aside (%)", "Never invested.", "float", True),
    ("paper", "starting_capital", "Paper starting capital (£)", "Used when a NEW paper account is created (after a reset).", "float", False),
    ("paper", "cost_profile", "Cost profile", "Which broker's charges to simulate (new accounts and backtests).", "choice:costs.profiles", False),
    ("data", "provider", "Market data provider", "Where prices come from. 'synthetic' is fake data for testing only.", "choice:yahoo,stooq,eodhd,synthetic", True),
    ("data", "fallback_provider", "Fallback data provider", "Used only if the main provider fails.", "choice:stooq,yahoo,eodhd", False),
    ("app", "auto_daily_cycle", "Run the daily paper cycle automatically", "Only while the app is open. Off = you press the button.", "bool", False),
    ("app", "daily_cycle_time", "Daily cycle time (London, HH:MM)", "Should be after free data has updated (about 17:30 or later).", "str", False),
    ("account", "type", "Account type for tax records", "gia = General Investment Account; isa = Stocks & Shares ISA.", "choice:gia,isa", False),
    ("ai", "enabled", "Use the local AI model", "Explanations only. The bot works the same without it.", "bool", False),
    ("ai", "model", "Ollama model name", "e.g. llama3.1:8b", "str", False),
    ("ai", "keep_alive", "Unload model after idle", "e.g. 5m frees GPU memory 5 minutes after the last request.", "str", False),
    ("notifications", "email_enabled", "Email alerts", "Requires SMTP details in the .env file (see docs/SETUP.md).", "bool", False),
]


def _fmt_money(v):
    if v is None:
        return "–"
    return f"-£{abs(v):,.2f}" if v < 0 else f"£{v:,.2f}"


def _fmt_pct(v, nd=2):
    return "–" if v is None else f"{v:+.{nd}f}%"


def create_app(ctx) -> FastAPI:
    app = FastAPI(title="UK Trading Bot", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["money"] = _fmt_money
    templates.env.filters["pct"] = _fmt_pct
    templates.env.filters["num"] = lambda v, nd=2: "–" if v is None else f"{v:,.{nd}f}"
    csrf = secrets.token_urlsafe(32)
    app.state.ctx = ctx
    app.state.csrf = csrf
    app.state.shutdown = None

    def page(request: Request, name: str, **kw):
        acc = ctx.service.account()
        rs = ctx.db.one("SELECT * FROM risk_state WHERE account_id=?", (acc["id"],))
        unread = ctx.db.one("SELECT COUNT(*) FROM alerts WHERE acknowledged=0 AND level!='info'")[0]
        base = {"request": request, "csrf": csrf, "banner": views.mode_banner(ctx.settings), "version": __version__,
                "kill": bool(rs and rs["kill_switch"]), "paused": rs["paused_reason"] if rs else None,
                "unread": unread, "market": cal.market_status(), "now": cal.now_london().strftime("%a %d %b %Y %H:%M"),
                "bg": ctx.background, "data_note": ctx.market.primary.delay_note, "page_name": name}
        base.update(kw)
        return templates.TemplateResponse(request, f"{name}.html", base)

    def check_csrf(token: str):
        if not secrets.compare_digest(token or "", csrf):
            raise HTTPException(403, "Security check failed - reload the page and try again.")

    @app.exception_handler(Exception)
    async def errors(request: Request, exc: Exception):
        log.exception("dashboard error on %s", request.url.path)
        return HTMLResponse("<h2>Something went wrong.</h2><p>The error has been written to the log file "
                            "(logs/tradebot.log). No orders were affected by this page error.</p>"
                            '<p><a href="/">Back to overview</a></p>', status_code=500)

    @app.exception_handler(HTTPException)
    async def http_errors(request: Request, exc: HTTPException):
        return HTMLResponse(f"<h2>{exc.status_code}</h2><p>{exc.detail}</p><p><a href='/'>Back</a></p>",
                            status_code=exc.status_code)

    def back(to: str = "/", msg: str | None = None):
        url = to + (("&" if "?" in to else "?") + "msg=" + msg.replace(" ", "+")[:300] if msg else "")
        return RedirectResponse(url, status_code=303)

    # ------------------------------------------------------------------ pages
    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request, msg: str | None = None):
        s = views.account_summary(ctx)
        dh = views.data_health(ctx)
        snaps = s.get("snaps", [])
        chart = line_chart([("Paper equity", [(x["date"], x["equity_gbp"]) for x in snaps])])
        last_run = ctx.db.one("SELECT * FROM runs WHERE account_id=? ORDER BY id DESC LIMIT 1", (s["account"]["id"],))
        return page(request, "overview", s=s, dh=dh, chart=chart, msg=msg, last_run=dict(last_run) if last_run else None,
                    alerts=ctx.alerts.recent(12), ai=ctx.analyst.status())

    @app.get("/market", response_class=HTMLResponse)
    def market(request: Request, msg: str | None = None):
        return page(request, "market", wl=views.watchlist_table(ctx), dh=views.data_health(ctx),
                    facts=views.market_facts(ctx), msg=msg)

    @app.get("/opportunities", response_class=HTMLResponse)
    def opportunities(request: Request, msg: str | None = None):
        return page(request, "opportunities", sigs=views.signals(ctx, 14), msg=msg,
                    pending=[o for o in views.orders(ctx) if o["status"] in ("PENDING", "PARTIAL")])

    @app.get("/portfolio", response_class=HTMLResponse)
    def portfolio(request: Request, msg: str | None = None):
        return page(request, "portfolio", s=views.account_summary(ctx), msg=msg)

    @app.get("/history", response_class=HTMLResponse)
    def history(request: Request, msg: str | None = None):
        return page(request, "history", orders=views.orders(ctx), fills=views.fills(ctx), tax=views.tax_summary(ctx), msg=msg)

    @app.get("/strategies", response_class=HTMLResponse)
    def strategies(request: Request, msg: str | None = None):
        items = []
        for key, cls in REGISTRY.items():
            conf = ctx.settings["strategies"].get(key, {}) or {}
            st = cls(conf.get("params") or {})
            items.append({**st.describe(), "enabled": bool(conf.get("enabled"))})
        return page(request, "strategies", items=items, msg=msg)

    @app.get("/risk", response_class=HTMLResponse)
    def risk(request: Request, msg: str | None = None):
        s = views.account_summary(ctx)
        return page(request, "risk", s=s, r=views.risk_view(ctx, s), msg=msg)

    @app.get("/backtest", response_class=HTMLResponse)
    def backtest(request: Request, run: int | None = None, msg: str | None = None):
        runs = [dict(r) for r in ctx.db.query("SELECT id,created_at,status,label,error FROM backtest_runs ORDER BY id DESC LIMIT 30")]
        sel = None
        chart = ""
        if run is not None:
            row = ctx.db.one("SELECT * FROM backtest_runs WHERE id=?", (run,))
            if row and row["result"]:
                sel = {**dict(row), "result": json.loads(row["result"]), "request": json.loads(row["request"])}
                res = sel["result"]
                if res.get("kind") == "research":
                    hc = res["holdout_chosen_full"]
                    chart = line_chart([("Strategy (holdout)", hc["equity_curve"]), ("Buy & hold benchmark", hc["benchmark_curve"]),
                                        ("Cash", hc["cash_curve"])])
                else:
                    chart = line_chart([("Strategy", res["equity_curve"]), ("Buy & hold benchmark", res["benchmark_curve"]),
                                        ("Cash", res["cash_curve"])])
            elif row:
                sel = {**dict(row), "result": None, "request": json.loads(row["request"])}
        uni = [u for u in load_universe(ctx.settings) if u["enabled"]]
        have = {r[0] for r in ctx.db.query("SELECT DISTINCT symbol FROM bars")}
        return page(request, "backtest", runs=runs, sel=sel, chart=chart, msg=msg, universe=uni, have=have,
                    strategies=REGISTRY, today=date.today().isoformat())

    @app.get("/ai", response_class=HTMLResponse)
    def ai(request: Request, msg: str | None = None):
        return page(request, "ai", status=ctx.analyst.status(), notes=ctx.analyst.recent(30), msg=msg,
                    sigs=views.signals(ctx, 14)[:20])

    @app.get("/reports", response_class=HTMLResponse)
    def reports(request: Request, view: str | None = None, msg: str | None = None):
        folder = paths.EXPORT_DIR / "reports"
        files = sorted(folder.glob("*.md"), reverse=True)[:60] if folder.exists() else []
        content = None
        if view:
            f = (folder / view).resolve()
            if f.parent == folder.resolve() and f.exists():
                content = f.read_text(encoding="utf-8")
        exports = sorted([p for p in paths.EXPORT_DIR.iterdir() if p.is_dir() and p.name != "reports"], reverse=True)[:20] \
            if paths.EXPORT_DIR.exists() else []
        return page(request, "reports", files=[f.name for f in files], content=content, view=view, msg=msg,
                    exports=[e.name for e in exports], backups=sorted([b.name for b in paths.BACKUP_DIR.glob("*.zip")], reverse=True)[:20])

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request, msg: str | None = None):
        fields = []
        for sec, key, label, help_, kind, prot in SETTINGS_FIELDS:
            choices = None
            if kind.startswith("choice:"):
                spec = kind.split(":", 1)[1]
                choices = list(ctx.settings["costs"]["profiles"]) if spec == "costs.profiles" else spec.split(",")
            fields.append({"name": f"{sec}.{key}", "label": label, "help": help_, "kind": kind.split(":")[0],
                           "value": ctx.settings[sec].get(key), "protected": prot, "choices": choices})
        uni = load_universe(ctx.settings)
        return page(request, "settings", fields=fields, msg=msg, mode=ctx.settings["app"]["mode"], universe=uni,
                    audit=[dict(r) for r in ctx.db.query("SELECT * FROM settings_audit ORDER BY id DESC LIMIT 20")])

    @app.get("/alerts", response_class=HTMLResponse)
    def alerts_page(request: Request, msg: str | None = None):
        return page(request, "alerts", alerts=ctx.alerts.recent(300), msg=msg)

    # ------------------------------------------------------------------ JSON
    @app.get("/api/status")
    def api_status():
        acc = ctx.service.account()
        return JSONResponse({"mode": ctx.settings["app"]["mode"], "account": acc["id"],
                             "background": {k: {kk: vv for kk, vv in v.items() if kk != "result"} for k, v in ctx.background.items()},
                             "market": cal.market_status(), "time": datetime.now(timezone.utc).isoformat()})

    @app.get("/download/{folder}/{name}")
    def download(folder: str, name: str):
        base = paths.EXPORT_DIR.resolve()
        f = (base / folder / name).resolve()
        if base not in f.parents or not f.is_file():
            raise HTTPException(404, "Not found")
        return FileResponse(f, filename=name)

    # ------------------------------------------------------------------ actions
    @app.post("/action/cycle")
    def act_cycle(csrf_token: str = Form(...), skip_update: str = Form("")):
        check_csrf(csrf_token)
        started = ctx.run_background("daily_cycle", lambda: ctx.service.run_daily_cycle(update_data=not skip_update))
        return back("/", "Daily paper cycle started - this page will show the result when it finishes"
                    if started else "A daily cycle is already running")

    @app.post("/action/update-data")
    def act_update(csrf_token: str = Form(...)):
        check_csrf(csrf_token)
        def job():
            res = ctx.market.update_all(ctx.service.symbols())
            bad = [r.symbol for r in res if not r.ok]
            return {"message": f"Updated {len(res) - len(bad)} of {len(res)}" + (f"; failed: {', '.join(bad[:10])}" if bad else "")}
        ok = ctx.run_background("data_update", job)
        return back("/market", "Data update started" if ok else "A data update is already running")

    @app.post("/action/kill")
    def act_kill(csrf_token: str = Form(...), on: str = Form(...)):
        check_csrf(csrf_token)
        ctx.service.set_kill_switch(on == "1")
        return back("/risk", "EMERGENCY STOP is ON" if on == "1" else "Emergency stop released")

    @app.post("/action/resume")
    def act_resume(csrf_token: str = Form(...), confirm: str = Form("")):
        check_csrf(csrf_token)
        if confirm != "yes":
            return back("/risk", "Tick the confirmation box to resume")
        ctx.service.resume()
        return back("/risk", "Trading pause cleared")

    @app.post("/action/flatten")
    def act_flatten(csrf_token: str = Form(...), confirm: str = Form("")):
        check_csrf(csrf_token)
        if confirm != "CLOSE ALL":
            return back("/risk", "Type CLOSE ALL to confirm")
        n = len(ctx.service.flatten_all())
        return back("/risk", f"{n} sell order(s) placed for the next session open (paper)")

    @app.post("/action/reset")
    def act_reset(csrf_token: str = Form(...), confirm: str = Form("")):
        check_csrf(csrf_token)
        try:
            res = ctx.service.reset_account(confirm)
        except ValueError as exc:
            return back("/settings", str(exc))
        return back("/", f"Old account exported to {Path(res['export']['folder']).name} and archived; new paper account created")

    @app.post("/action/ack-alerts")
    def act_ack(csrf_token: str = Form(...)):
        check_csrf(csrf_token)
        ctx.alerts.acknowledge_all()
        return back("/alerts", "Alerts marked as read")

    @app.post("/action/clear-suspicious")
    def act_clear(csrf_token: str = Form(...), symbol: str = Form(...)):
        check_csrf(csrf_token)
        ctx.market.clear_suspicious(symbol.strip().upper())
        return back("/market", f"Flag cleared for {symbol}")

    def _save(new: dict, changes: list[tuple[str, object, object]]):
        save_user_settings(new)
        for path, old, val in changes:
            ctx.db.execute("INSERT INTO settings_audit(ts,path,old_value,new_value) VALUES (?,?,?,?)",
                           (datetime.now(timezone.utc).isoformat(timespec="seconds"), path, json.dumps(old), json.dumps(val)))
        ctx.reload_settings()

    @app.post("/action/settings")
    async def act_settings(request: Request):
        form = await request.form()
        check_csrf(form.get("csrf_token", ""))
        new = copy.deepcopy(ctx.settings)
        changes = []
        for sec, key, label, _, kind, prot in SETTINGS_FIELDS:
            name = f"{sec}.{key}"
            old = ctx.settings[sec].get(key)
            if kind == "bool":
                val = form.get(name) == "on"
            elif name not in form:
                continue
            else:
                raw = str(form.get(name)).strip()
                try:
                    val = float(raw) if kind == "float" else int(float(raw)) if kind == "int" else raw
                except ValueError:
                    return back("/settings", f"{label}: please enter a number")
            if val != old:
                if prot and form.get("confirm_protected") != "on":
                    return back("/settings", f"'{label}' is a protected setting - tick the confirmation box to change it")
                new[sec][key] = val
                changes.append((name, old, val))
        if not changes:
            return back("/settings", "No changes")
        try:
            _save(new, changes)
        except ConfigError as exc:
            return back("/settings", str(exc))
        return back("/settings", f"Saved {len(changes)} change(s). A backup of the previous settings file was kept.")

    @app.post("/action/mode")
    def act_mode(csrf_token: str = Form(...), mode: str = Form(...), confirm: str = Form("")):
        check_csrf(csrf_token)
        if mode != "local_paper":
            return back("/settings", "Only local paper trading is available in this version. Live trading requires a "
                                     "separate readiness review (docs/LIVE_TRADING.md).")
        if confirm != "PAPER":
            return back("/settings", "Type PAPER to confirm")
        new = copy.deepcopy(ctx.settings)
        old = new["app"]["mode"]
        new["app"]["mode"] = "local_paper"
        _save(new, [("app.mode", old, "local_paper")])
        return back("/settings", "Mode set to local paper trading")

    @app.post("/action/strategy")
    async def act_strategy(request: Request):
        form = await request.form()
        check_csrf(form.get("csrf_token", ""))
        key = form.get("key")
        if key not in REGISTRY:
            raise HTTPException(400, "Unknown strategy")
        cls = REGISTRY[key]
        params = {}
        try:
            for p in cls.param_specs:
                if f"p_{p.name}" in form:
                    params[p.name] = p.coerce(form.get(f"p_{p.name}"))
        except ValueError as exc:
            return back("/strategies", str(exc))
        new = copy.deepcopy(ctx.settings)
        conf = new["strategies"].setdefault(key, {})
        old = copy.deepcopy(conf)
        conf["enabled"] = form.get("enabled") == "on"
        defaults = {p.name: p.default for p in cls.param_specs}
        conf["params"] = {k: v for k, v in params.items() if v != defaults[k]}
        if form.get("reset_defaults") == "on":
            conf["params"] = {}
        if conf == old:
            return back("/strategies", "No changes")
        if (conf.get("params") or {}) != (old.get("params") or {}) and form.get("confirm") != "on":
            return back("/strategies", "Tick the confirmation box to change strategy parameters")
        _save(new, [(f"strategies.{key}", old, conf)])
        return back("/strategies", f"Saved {cls.name}")

    @app.post("/action/backtest")
    async def act_backtest(request: Request):
        form = await request.form()
        check_csrf(form.get("csrf_token", ""))
        strategies = [k for k in REGISTRY if form.get(f"s_{k}") == "on"]
        if not strategies:
            return back("/backtest", "Choose at least one strategy")
        symbols = form.getlist("symbols") or [u["symbol"] for u in load_universe(ctx.settings) if u["enabled"]]
        try:
            start = date.fromisoformat(str(form.get("start")))
            end = date.fromisoformat(str(form.get("end")))
            capital = float(form.get("capital") or 10000)
        except ValueError:
            return back("/backtest", "Check the dates and capital")
        kind = form.get("kind", "single")
        synthetic = form.get("synthetic") == "on"
        profile = form.get("cost_profile") or ctx.settings["paper"]["cost_profile"]
        reqd = {"strategies": strategies, "symbols": symbols, "start": start.isoformat(), "end": end.isoformat(),
                "capital": capital, "kind": kind, "synthetic": synthetic, "cost_profile": profile}
        cur = ctx.db.execute("INSERT INTO backtest_runs(created_at,status,label,request) VALUES (?,?,?,?)",
                             (datetime.now(timezone.utc).isoformat(timespec="seconds"), "running",
                              f"{kind}: {', '.join(strategies)} {start}..{end}" + (" [SYNTHETIC]" if synthetic else ""),
                              json.dumps(reqd)))
        run_id = cur.lastrowid

        def job():
            try:
                bench = ctx.settings["universe"]["benchmark"]
                if synthetic:
                    from datetime import timedelta
                    view = synthetic_view(list(dict.fromkeys(symbols + [bench])), load_universe(ctx.settings),
                                          start - timedelta(days=500), end)
                else:
                    view = load_view(ctx.market, symbols, bench)
                if kind == "research":
                    res = run_research(ctx.settings, view, strategies[0], symbols, start, end, capital, workers=1)
                    res["kind"] = "research"
                    res["data_source_note"] = view.source_note
                    md = research_markdown(res, view.source_note)
                    f = paths.EXPORT_DIR / "reports" / f"research-{strategies[0]}-{date.today().isoformat()}-{run_id}.md"
                    f.parent.mkdir(parents=True, exist_ok=True)
                    f.write_text(md, encoding="utf-8")
                    res["report_file"] = f.name
                else:
                    res = run_backtest(ctx.settings, view, BacktestRequest(strategies=strategies, symbols=symbols, start=start,
                                                                            end=end, starting_capital=capital, cost_profile=profile))
                    res["kind"] = "single"
                    res["data_source_note"] = view.source_note
                ctx.db.execute("UPDATE backtest_runs SET status='done', result=? WHERE id=?", (json.dumps(res, default=str), run_id))
                return {"message": f"Backtest #{run_id} finished"}
            except Exception as exc:
                ctx.db.execute("UPDATE backtest_runs SET status='failed', error=? WHERE id=?", (f"{type(exc).__name__}: {exc}", run_id))
                raise

        ctx.run_background(f"backtest_{run_id}", job)
        return back(f"/backtest?run={run_id}", f"Backtest #{run_id} started; refresh in a moment")

    @app.post("/action/ai/explain")
    def act_explain(csrf_token: str = Form(...), signal_id: int = Form(...)):
        check_csrf(csrf_token)
        row = ctx.db.one("SELECT * FROM signals WHERE id=?", (signal_id,))
        if not row:
            raise HTTPException(404, "Signal not found")
        ok = ctx.run_background("ai", lambda: ctx.analyst.explain_signal(dict(row)) or {"message": "AI explanation finished"})
        return back("/ai", "Asking the local model - refresh in a few seconds" if ok else "The AI is busy")

    @app.post("/action/ai/market")
    def act_ai_market(csrf_token: str = Form(...)):
        check_csrf(csrf_token)
        facts = views.market_facts(ctx)
        ok = ctx.run_background("ai", lambda: ctx.analyst.market_summary(facts))
        return back("/ai", "Asking the local model - refresh in a few seconds" if ok else "The AI is busy")

    @app.post("/action/report")
    def act_report(csrf_token: str = Form(...), kind: str = Form("daily"), ai: str = Form("")):
        check_csrf(csrf_token)
        acc = ctx.service.account()
        last = ctx.db.one("SELECT MAX(date) FROM equity_snapshots WHERE account_id=?", (acc["id"],))[0]
        end = date.fromisoformat(last) if last else cal.last_completed_session()
        text = None
        if ai == "on":
            from ..reports.generator import build_report_data
            d = build_report_data(ctx.db, acc["id"], end if kind == "daily" else end.fromordinal(end.toordinal() - 6), end,
                                  ctx.settings["universe"]["benchmark"], ctx.market)
            facts = {"period": f"{d['start']}..{d['end']}", "equity_change_gbp": d["equity_change_gbp"],
                     "equity_change_pct": d["equity_change_pct"], "since_start_pct": d["since_start_pct"],
                     "fees_gbp": d["fees_gbp"], "spread_slippage_gbp": d["impact_gbp"], "fills": len(d["fills"]),
                     "benchmark_return_pct": d["benchmark_return_pct"], "signals": len(d["signals"]),
                     "top_rejections": d["rejections"][:5], "warnings": [a["message"] for a in d["alerts"][-10:]]}
            out = ctx.analyst.report_commentary(facts)
            text = out.get("text") if out.get("ok") else f"(AI unavailable: {out.get('message')})"
        res = generate(ctx.db, acc["id"], kind if kind in ("daily", "weekly") else "daily", end,
                       ctx.settings["universe"]["benchmark"], ctx.market, ai_text=text)
        ctx.alerts.raise_("info", "report", f"{kind.capitalize()} report generated: {Path(res['path']).name}")
        return back(f"/reports?view={Path(res['path']).name}", "Report generated")

    @app.post("/action/export")
    def act_export(csrf_token: str = Form(...)):
        check_csrf(csrf_token)
        acc = ctx.service.account()
        res = export_account(ctx.db, acc["id"], "paper-export", ctx.settings["account"]["type"])
        return back("/reports", f"Exported {len(res['files'])} CSV files to exports/{Path(res['folder']).name}")

    @app.post("/action/backup")
    def act_backup(csrf_token: str = Form(...)):
        check_csrf(csrf_token)
        f = create_backup(ctx.db)
        return back("/reports", f"Backup saved: backups/{f.name}")

    @app.post("/action/shutdown")
    def act_shutdown(token: str = Form(...)):
        expected = paths.CONTROL_TOKEN_FILE.read_text(encoding="utf-8").strip() if paths.CONTROL_TOKEN_FILE.exists() else None
        if not expected or not secrets.compare_digest(token, expected):
            raise HTTPException(403, "bad token")
        if app.state.shutdown:
            app.state.shutdown()
        return JSONResponse({"ok": True})

    return app
