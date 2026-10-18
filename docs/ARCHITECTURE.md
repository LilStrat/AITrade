# Architecture

```
            ┌────────────── dashboard (FastAPI, 127.0.0.1 only) ──────────────┐
            │  pages · CSRF-protected actions · background jobs · SVG charts  │
            └───────────────┬───────────────────────────────┬─────────────────┘
                            │                               │
                 paper_trading/service.py          backtesting/engine.py, research.py
                 (daily cycle, catch-up,            (in-memory DB, simulated clock,
                  idempotency, failure pause)        metrics, walk-forward, verdicts)
                            │                               │
                            └──────────► core/trading.py ◄──┘   ONE shared daily cycle
                                          │    │      │
              strategies/ (propose) ──────┘    │      └────── risk/manager.py (size + approve/reject)
                                               │
                     brokers/paper.py (local simulated broker) ── execution.py (fill model) ── costs.py
                                               │
                     portfolio/ledger.py (append-only GBP ledger) ── portfolio/tax.py (estimates)
                                               │
          data/store.py ◄── data/providers/{yahoo, stooq, eodhd, synthetic}   data/validation.py, calendar.py
                                               │
                                     db.py (SQLite, migrations, triggers)

  ai/ (Ollama client + analyst): reads verified facts, writes text notes only. Cannot reach brokers, core or risk.
```

## Key design decisions

* **Backtest = paper trading code.** The backtester runs the same
  `TradingCore`, `LocalPaperBroker`, risk manager and ledger as paper trading,
  on an in-memory database with a simulated clock. A behaviour difference
  between the two is therefore a bug, not a modelling choice.
* **Timing contract.** Decide after the close of day *t*; orders carry
  `created_for_date = t` and can only fill on sessions after *t*. This is
  enforced in the broker, not just in the strategies.
* **Prices in pounds.** LSE pence (GBX) quotes are converted at the data
  boundary; isolated 100× unit errors are repaired and logged.
* **Split-adjusted prices, dividends as cash.** Backtests use the provider's
  split-adjusted history and credit dividends on ex-dates. In paper trading, a
  newly reported split adjusts the position and its stops, and a restated
  history triggers a full reload.
* **Append-only ledger.** Positions, cash and P&L are always recomputed from
  transactions. SQLite triggers block UPDATE and DELETE. A unique `ref` per
  economic event prevents double counting.
* **Broker adapter interface** (`brokers/base.py`): idempotent submit by client
  order ID; look an order up before ever re-submitting after a timeout; never
  any fund transfers. A future IBKR or Trading 212 adapter plugs in here.
* **Modes.** `local_paper` (default and only order-capable mode in v1),
  `broker_paper` and `live` (both refused by `brokers/live_guard.py`).
  Backtesting is separate and never touches an account. There is no fallback
  path from paper to live.

## Folder map

| Folder / file | Purpose |
|---|---|
| `app.py` | Entry point and commands (run, stop, cycle, backtest, research, report, export, backup, restore, selfcheck) |
| `tradebot/config.py` | Defaults + your overrides, validation, locked settings, `.env` secrets |
| `tradebot/data/` | Providers, validation, LSE calendar (bank holidays, DST), storage, freshness |
| `tradebot/strategies/` | Strategy interface, two strategies, regime filter |
| `tradebot/risk/` | Position sizing and all pre-trade checks |
| `tradebot/core/` | Shared daily cycle and data views |
| `tradebot/brokers/` | Adapter interface, local paper broker, live-trading guard |
| `tradebot/execution.py`, `costs.py` | Fill model and cost model |
| `tradebot/portfolio/` | Ledger/accounting and UK tax-record estimates |
| `tradebot/backtesting/` | Engine, metrics, walk-forward research |
| `tradebot/paper_trading/` | Daily cycle service, controls, reset |
| `tradebot/ai/` | Optional Ollama integration |
| `tradebot/reports/` | Daily/weekly reports, CSV export |
| `tradebot/dashboard/` | Web UI |
| `config/` | `default_settings.yaml` (shipped), `universe.yaml` (your watchlist), `settings.yaml` (your overrides, private) |
| `database/`, `logs/`, `exports/`, `backups/` | Private runtime data (excluded from Git) |
| `tests/` | Automated tests (synthetic data, no network) |
