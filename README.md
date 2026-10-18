# UK Trading Bot - stocks & ETFs, paper trading first

A locally run trading application for a UK resident trading London-listed
shares and ETFs. It includes:

* **Market data:** daily prices for a configurable watchlist from free sources,
  with validation, pence→pound conversion, freshness checks and visible data
  source and delay
* **Two documented strategies** (trend pullback, breakout momentum) with a
  market-regime filter
* **Independent risk manager:** position sizing from stop distance; daily-loss,
  drawdown, exposure, sector, correlation, liquidity and cost limits; emergency
  stop
* **Backtesting** with commissions, UK stamp duty, spread, slippage, gaps and
  volume limits, plus walk-forward research with an untouched holdout and
  pre-registered pass/fail criteria
* **Local paper trading** (default mode): persistent simulated account,
  append-only GBP ledger, stops, dividends, safe catch-up after downtime
* **Dashboard** in your browser (only on your PC), daily/weekly reports, CSV
  exports, UK tax-record estimates, backups
* **Optional local AI** (Ollama / Llama 3.1 8B) for plain-English
  explanations. It cannot place orders or change risk limits.

> **Status:** v1.0.0. All 122 automated tests pass. Live trading is **disabled**:
> there is no live broker adapter. No strategy has yet been tested on real
> market data (the build environment had no market data access), so **nothing
> here shows the bot can make money**. Step 6 of the setup guide produces the
> first real evidence.

## Quick start (Windows)

Full beginner instructions: **[docs/SETUP.md](docs/SETUP.md)**.

1. Put the files in `C:\AI\Trading Bot` (so that `C:\AI\Trading Bot\app.py` exists).
2. Double-click **`1 - First Time Setup.bat`** (uses your Python 3.14; installs into `.venv` only).
3. Double-click **`2 - Start Trading Bot.bat`**. The dashboard opens at http://127.0.0.1:8765.
4. Click **Run daily cycle now** (first price download takes a few minutes).
5. Double-click **`Run Research Backtests.bat`** and read the verdicts.

**Smart App Control blocking the `.bat` files?** Don't turn it off. See the
[Smart App Control section](docs/SETUP.md#if-windows-says-smart-app-control-blocked)
for the one-line unblock, or use Python directly: `py -3.14 setup_windows.py` once,
then `py app.py` to start. Every dashboard page has a **Stop the trading bot** button.

| Launcher | What it does |
|---|---|
| `1 - First Time Setup.bat` | Creates `.venv`, installs pinned packages, initialises the database, runs a self-check |
| `2 - Start Trading Bot.bat` | Starts the dashboard (keep the window open) |
| `3 - Stop Trading Bot.bat` | Stops it cleanly |
| `Download Prices.bat` | Updates prices without running the strategies |
| `Run Research Backtests.bat` | Walk-forward + holdout research report per strategy |
| `Run Tests.bat` | Runs the automated test suite |
| `Backup Now.bat` / `Restore Backup.bat` | Database & settings backup / restore |

## Documentation

| Document | Contents |
|---|---|
| [docs/BROKER_RESEARCH.md](docs/BROKER_RESEARCH.md) | Broker & data comparison with sources, recommendation, costs |
| [docs/SETUP.md](docs/SETUP.md) | Step-by-step installation and daily use |
| [docs/STRATEGIES.md](docs/STRATEGIES.md) | Exact rules, rationale, risks, research protocol, evidence status |
| [docs/RISK.md](docs/RISK.md) | Every risk limit, what changing it means, stop-loss caveats |
| [docs/UK_TAX_AND_ACCOUNTS.md](docs/UK_TAX_AND_ACCOUNTS.md) | GIA vs ISA, CGT matching rules, stamp duty, record keeping, regulation |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How the parts fit together |
| [docs/AI_ASSISTANT.md](docs/AI_ASSISTANT.md) | What the local model does and doesn't do; hardware notes |
| [docs/LIVE_TRADING.md](docs/LIVE_TRADING.md) | Why live trading is off and the readiness checklist |
| [docs/LIMITATIONS_AND_MAINTENANCE.md](docs/LIMITATIONS_AND_MAINTENANCE.md) | Test status, known limitations, ongoing costs, upkeep |

## Safety principles

* Paper mode by default; no silent switch to real money; no code path for fund
  transfers.
* Doing nothing is a valid outcome. The bot holds cash when no setup passes
  every rule and risk check.
* Data that is stale, suspicious or of unknown currency blocks trading in that
  instrument.
* The dashboard listens on 127.0.0.1 only. Secrets live only in `.env`.
  Database, logs, exports, backups and your settings are excluded from Git.
* Past or simulated performance does not predict future results.

## Command line (optional)
```
.venv\Scripts\python.exe app.py --help
```
