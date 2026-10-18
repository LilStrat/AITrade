"""UK Trading Bot - application entry point.

Usually started from the Windows launcher files. Commands:

  python app.py run            start the dashboard (http://127.0.0.1:8765)
  python app.py stop           stop a running dashboard
  python app.py init           create folders, settings and database
  python app.py update-data    download/refresh prices
  python app.py cycle          run the daily paper-trading cycle once
  python app.py backtest ...   run a backtest from the command line
  python app.py research ...   walk-forward + holdout research report
  python app.py report daily|weekly
  python app.py export         export history and tax records to CSV
  python app.py backup | restore <file>
  python app.py selfcheck      check the installation
"""

from __future__ import annotations

import argparse
import json
import logging
import secrets
import sys
import threading
import webbrowser
from datetime import date, timedelta

from tradebot import __version__, paths

log = logging.getLogger("tradebot")


def _ctx():
    from tradebot.runtime import AppContext
    return AppContext()


def cmd_init(args) -> int:
    ctx = _ctx()
    acc = ctx.service.account()
    print(f"Project folder : {paths.PROJECT_ROOT}")
    print(f"Database       : {paths.DB_FILE}")
    print(f"Settings       : {paths.USER_SETTINGS_FILE}")
    print(f"Paper account  : #{acc['id']} with GBP {acc['starting_capital']:,.2f} (simulated)")
    print(f"Instruments    : {len(ctx.market.instruments())} enabled")
    return 0


def cmd_run(args) -> int:
    import uvicorn

    from tradebot.dashboard.server import create_app
    from tradebot.lock import AlreadyRunning, InstanceLock

    lock = InstanceLock(paths.LOCK_FILE)
    try:
        lock.acquire()
    except AlreadyRunning as exc:
        print(str(exc))
        print(f"Open http://127.0.0.1:{args.port or 8765} in your browser, or run 'Stop Trading Bot.bat' first.")
        return 2
    try:
        ctx = _ctx()
        port = args.port or int(ctx.settings["app"]["dashboard_port"])
        paths.CONTROL_TOKEN_FILE.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        (paths.DB_DIR / "port").write_text(str(port), encoding="utf-8")
        app = create_app(ctx)
        # 127.0.0.1 only: the dashboard is never reachable from other computers.
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
        server = uvicorn.Server(config)
        app.state.shutdown = lambda: setattr(server, "should_exit", True)
        ctx.start_scheduler()
        url = f"http://127.0.0.1:{port}/"
        print(f"\n  UK Trading Bot v{__version__} - PAPER TRADING (simulated money)")
        print(f"  Dashboard: {url}")
        print("  Close this window or run 'Stop Trading Bot.bat' to stop.\n")
        if not args.no_browser:
            threading.Timer(1.5, lambda: webbrowser.open(url)).start()
        server.run()
        ctx.stop()
        print("Stopped cleanly.")
        return 0
    finally:
        try:
            paths.CONTROL_TOKEN_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        lock.release()


def cmd_stop(args) -> int:
    import httpx
    if not paths.CONTROL_TOKEN_FILE.exists():
        print("The trading bot does not appear to be running.")
        return 0
    port = (paths.DB_DIR / "port").read_text().strip() if (paths.DB_DIR / "port").exists() else "8765"
    try:
        r = httpx.post(f"http://127.0.0.1:{port}/action/shutdown",
                       data={"token": paths.CONTROL_TOKEN_FILE.read_text(encoding="utf-8").strip()}, timeout=10)
        print("Stop requested." if r.status_code == 200 else f"Stop failed (HTTP {r.status_code}).")
        return 0 if r.status_code == 200 else 1
    except httpx.HTTPError:
        print("Could not reach the running app. If a window is still open, close it.")
        return 1


def cmd_update(args) -> int:
    ctx = _ctx()
    syms = [s.upper() for s in args.symbols] if args.symbols else ctx.service.symbols()

    def progress(i, n, r):
        print(f"[{i}/{n}] {r.symbol:10s} {'OK ' if r.ok else 'FAIL'} {r.message}")
    res = ctx.market.update_all(syms, progress=progress)
    bad = [r for r in res if not r.ok]
    print(f"\nDone: {len(res) - len(bad)} ok, {len(bad)} failed. Source: {ctx.market.primary.describe()['delay_note']}")
    return 1 if bad and len(bad) == len(res) else 0


def cmd_cycle(args) -> int:
    ctx = _ctx()
    res = ctx.service.run_daily_cycle(update_data=not args.no_update)
    print(json.dumps(res, indent=2, default=str))
    return 0 if res.get("ok") else 1


def _auto_start(ctx, view, requested: str | None) -> date:
    """Default start = benchmark's first bar + ~14 months, so indicators (200-day averages) are warmed up."""
    if requested and requested != "auto":
        return date.fromisoformat(requested)
    h = view.history(ctx.settings["universe"]["benchmark"])
    if h.empty:
        raise SystemExit("No benchmark data downloaded yet - run 'Download Prices.bat' first.")
    return h.index[0] + timedelta(days=430)


def _view(ctx, symbols, synthetic: bool, start: date, end: date):
    from tradebot.backtesting.data import load_view, synthetic_view
    from tradebot.config import load_universe
    bench = ctx.settings["universe"]["benchmark"]
    if synthetic:
        return synthetic_view(list(dict.fromkeys(symbols + [bench])), load_universe(ctx.settings), start - timedelta(days=500), end)
    return load_view(ctx.market, symbols, bench)


def cmd_backtest(args) -> int:
    from tradebot.backtesting.engine import BacktestRequest, run_backtest
    ctx = _ctx()
    syms = [s.upper() for s in args.symbols] if args.symbols else [i["symbol"] for i in ctx.market.instruments()]
    end = date.fromisoformat(args.end)
    view = _view(ctx, syms, args.synthetic, date(2010, 1, 1), end)
    start = _auto_start(ctx, view, args.start)
    res = run_backtest(ctx.settings, view, BacktestRequest(strategies=args.strategy, symbols=syms, start=start, end=end,
                                                           starting_capital=args.capital, cost_profile=args.costs))
    m, t, b = res["metrics_after_costs"], res["trade_metrics"], res["benchmark"]["metrics"]
    print(f"\n{view.source_note}")
    print(f"Period {res['period']['start']} .. {res['period']['end']} ({res['period']['sessions']} sessions)")
    print(f"Return after costs : {m['total_return_pct']:+.2f}%   (before costs approx {res['metrics_before_costs_approx']['total_return_pct']:+.2f}%)")
    print(f"Max drawdown       : {m['max_drawdown_pct']:.2f}%   Sharpe {m['sharpe']:.2f}")
    print(f"Trades             : {t.get('trades', 0)}   win rate {t.get('win_rate_pct', 0):.1f}%   profit factor {t.get('profit_factor', 0):.2f}")
    print(f"Costs              : GBP {res['costs']['total_gbp']:,.2f}   exposure {res['exposure_pct']:.1f}%")
    print(f"Benchmark {res['benchmark']['symbol']:8s} : {b.get('total_return_pct', float('nan')):+.2f}%  max DD {b.get('max_drawdown_pct', float('nan')):.2f}%")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(res, fh, indent=1, default=str)
    return 0


def cmd_research(args) -> int:
    from tradebot.backtesting.research import research_markdown, run_research
    ctx = _ctx()
    syms = [s.upper() for s in args.symbols] if args.symbols else [i["symbol"] for i in ctx.market.instruments()]
    end = date.fromisoformat(args.end)
    view = _view(ctx, syms, args.synthetic, date(2010, 1, 1), end)
    start = _auto_start(ctx, view, args.start)
    print(f"Research period {start} .. {end} on {len(syms)} instruments ({view.source_note})")
    out = paths.EXPORT_DIR / "reports"
    out.mkdir(parents=True, exist_ok=True)
    for strat in args.strategy:
        print(f"\n=== Research: {strat} ===")
        res = run_research(ctx.settings, view, strat, syms, start, end, args.capital, workers=args.workers,
                           progress=lambda m: print("  ..", m, flush=True))
        md = research_markdown(res, view.source_note)
        f = out / f"research-{strat}-{date.today().isoformat()}{'-SYNTHETIC' if args.synthetic else ''}.md"
        f.write_text(md, encoding="utf-8")
        (f.with_suffix(".json")).write_text(json.dumps(res, default=str, indent=1), encoding="utf-8")
        print(f"  Verdict: {res['verdict']}  ({'; '.join(res['verdict_reasons']) or 'criteria met'})")
        print(f"  Report : {f}")
    return 0


def cmd_report(args) -> int:
    from tradebot.reports.generator import generate
    ctx = _ctx()
    acc = ctx.service.account()
    last = ctx.db.one("SELECT MAX(date) FROM equity_snapshots WHERE account_id=?", (acc["id"],))[0]
    end = date.fromisoformat(last) if last else date.today()
    res = generate(ctx.db, acc["id"], args.kind, end, ctx.settings["universe"]["benchmark"], ctx.market)
    print(res["markdown"])
    print(f"Saved to {res['path']}")
    return 0


def cmd_export(args) -> int:
    from tradebot.reports.export import export_account
    ctx = _ctx()
    res = export_account(ctx.db, ctx.service.account()["id"], "paper-export", ctx.settings["account"]["type"])
    print(f"Exported to {res['folder']}: {', '.join(res['files'])}")
    return 0


def cmd_backup(args) -> int:
    from tradebot.backup import create_backup
    print(f"Backup saved: {create_backup()}")
    return 0


def cmd_restore(args) -> int:
    from tradebot.backup import restore_backup
    from tradebot.lock import AlreadyRunning, InstanceLock
    lock = InstanceLock(paths.LOCK_FILE)
    try:
        lock.acquire()
    except AlreadyRunning:
        print("Stop the trading bot before restoring a backup.")
        return 2
    try:
        print(f"This will replace the current database and settings with: {args.file}")
        print("A safety backup of the current state is made first.")
        confirm = input("Type RESTORE to continue: ").strip()
        safety = restore_backup(args.file, confirm)
        print(f"Restored. Previous state saved as {safety}")
        return 0
    except ValueError as exc:
        print(f"Not restored: {exc}")
        return 1
    finally:
        lock.release()


def cmd_selfcheck(args) -> int:
    import importlib
    import platform
    ok = True
    print(f"Python {platform.python_version()} on {platform.system()} {platform.release()}")
    if sys.version_info < (3, 12):
        print("  WARNING: Python 3.12+ is recommended (built and tested on 3.14).")
    for mod in ("numpy", "pandas", "yaml", "httpx", "fastapi", "uvicorn", "jinja2", "yfinance", "tzdata"):
        try:
            m = importlib.import_module(mod)
            print(f"  ok   {mod} {getattr(m, '__version__', '')}")
        except Exception as exc:
            ok = False
            print(f"  FAIL {mod}: {exc}")
    from zoneinfo import ZoneInfo
    try:
        ZoneInfo("Europe/London")
        print("  ok   time zone database (Europe/London)")
    except Exception:
        ok = False
        print("  FAIL time zone database - run: python -m pip install tzdata")
    try:
        ctx = _ctx()
        print(f"  ok   database {paths.DB_FILE}")
        st = ctx.analyst.status()
        print(f"  {'ok  ' if st.get('available') else 'info'} local AI (Ollama): "
              f"{'available' if st.get('available') else 'not running (optional)'}"
              + (f", model installed: {st.get('model_installed')}" if st.get('available') else ""))
        print(f"  info data provider: {ctx.market.primary.describe()['delay_note']}")
    except Exception as exc:
        ok = False
        print(f"  FAIL application start: {type(exc).__name__}: {exc}")
    print("\nSelf-check", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


def main(argv=None) -> int:
    from tradebot.logging_setup import setup_logging
    p = argparse.ArgumentParser(prog="app.py", description="UK stock & ETF trading bot (paper trading by default)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").set_defaults(fn=cmd_init)
    r = sub.add_parser("run")
    r.add_argument("--port", type=int)
    r.add_argument("--no-browser", action="store_true")
    r.set_defaults(fn=cmd_run)
    sub.add_parser("stop").set_defaults(fn=cmd_stop)
    u = sub.add_parser("update-data")
    u.add_argument("symbols", nargs="*")
    u.set_defaults(fn=cmd_update)
    c = sub.add_parser("cycle")
    c.add_argument("--no-update", action="store_true")
    c.set_defaults(fn=cmd_cycle)
    for name, fn in (("backtest", cmd_backtest), ("research", cmd_research)):
        b = sub.add_parser(name)
        b.add_argument("--strategy", nargs="+", default=["trend_pullback", "breakout_momentum"])
        b.add_argument("--symbols", nargs="*")
        b.add_argument("--start", default="auto", help="YYYY-MM-DD or 'auto' (after indicator warm-up)")
        b.add_argument("--end", default=date.today().isoformat())
        b.add_argument("--capital", type=float, default=10_000)
        b.add_argument("--synthetic", action="store_true", help="use made-up data (machinery test only)")
        if name == "backtest":
            b.add_argument("--costs", default=None)
            b.add_argument("--json")
        else:
            b.add_argument("--workers", type=int, default=None)
        b.set_defaults(fn=fn)
    rp = sub.add_parser("report")
    rp.add_argument("kind", choices=["daily", "weekly"])
    rp.set_defaults(fn=cmd_report)
    sub.add_parser("export").set_defaults(fn=cmd_export)
    sub.add_parser("backup").set_defaults(fn=cmd_backup)
    rs = sub.add_parser("restore")
    rs.add_argument("file")
    rs.set_defaults(fn=cmd_restore)
    sub.add_parser("selfcheck").set_defaults(fn=cmd_selfcheck)
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        argv = ["run"]  # "py app.py" (or double-clicking app.py) starts the dashboard
    args = p.parse_args(argv)
    setup_logging(console=args.cmd not in ("run",))
    try:
        return args.fn(args)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130


def _use_project_venv() -> int | None:
    """If started with the system Python (e.g. "py app.py"), re-run inside the
    project's own .venv so the pinned packages are used."""
    import os
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parent
    venv_dir = root / ".venv"
    venv_py = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not venv_py.exists() or Path(sys.prefix).resolve() == venv_dir.resolve():
        return None
    env = {**os.environ, "PYTHONUTF8": "1"}
    try:
        return subprocess.run([str(venv_py), str(Path(__file__).resolve()), *sys.argv[1:]], env=env).returncode
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    code = _use_project_venv()
    sys.exit(main() if code is None else code)
