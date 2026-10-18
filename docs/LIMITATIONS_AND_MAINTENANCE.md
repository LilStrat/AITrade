# Limitations, testing status and maintenance

## Testing status (v1.0.0)

* **117 automated tests, all passing** on Python 3.14.8 (Linux build
  environment), 2.5 minutes. Coverage includes indicators (incl. Wilder's
  textbook RSI values and lookahead checks), LSE calendar and DST, data
  validation and failure handling, costs, fills (gaps, volume caps), ledger
  (append-only, duplicates, splits), risk checks, strategies, the paper broker
  (idempotency, no same-day fills, OCO), backtests (accounting identity,
  determinism, **future-data-change lookahead test**), the daily cycle
  (catch-up, idempotency, stale data, failure pause, restart recovery, kill
  switch, reset), tax matching rules, AI isolation, security (secrets, CSRF,
  host header, localhost binding, path traversal) and every dashboard page.
* **Not run in the build environment:** the Windows batch files, the
  Windows file lock (`msvcrt`), real network downloads (the build
  environment could not reach Yahoo/Stooq/EODHD), and your local Ollama.
  The code paths exist and are written for Windows, but **the first real
  confirmation is `Run Tests.bat` and `1 - First Time Setup.bat` on your PC**.

## Known limitations

* **End-of-day only.** Decisions after the close, execution at the next open.
  No intraday trading; no real-time quotes. Delayed data is labelled as such.
* **Free data is unofficial.** Yahoo can change or block access. The Stooq
  fallback has no currency metadata (it is only used after Yahoo has
  identified an instrument's currency once) and its adjustment basis is
  undocumented, so it is only accepted when it agrees with stored prices.
* **Bid/ask prices are not available** from free sources. Spreads are fixed
  estimates per instrument tier.
* **Survivorship bias.** The universe is today's large companies, so
  backtests are optimistic.
* **Corporate actions:** dividends and splits are handled. Mergers,
  spin-offs, rights issues and delistings are not; they would show as
  suspicious price moves and block trading in that instrument until you
  review it.
* **Paper fills are approximations** of the real open price and of stop
  behaviour (see `tradebot/execution.py`).
* **Tax output is an estimate** (see `UK_TAX_AND_ACCOUNTS.md`).
* **US-listed instruments** are supported (with FX costs) but disabled by
  default, and the trading calendar is the LSE's.
* **Email alerts** are implemented but untested against a real mail server.

## Ongoing costs
£0 by default. Optional: EODHD data (~$20/month), and later a broker's
commissions and data fees. See `BROKER_RESEARCH.md`.

## Maintenance
* Back up regularly (Reports page or `Backup Now.bat`) and copy backups off
  the PC.
* Logs are in `logs\tradebot.log` (rotated automatically, 10 × 5 MB).
* Check the **Market → Data quality log** weekly.
* Each April, check the CGT annual exempt amount, dividend allowance, ISA
  allowance and stamp duty rules, and update `tradebot/portfolio/tax.py` /
  `config` if they change.
* Re-run `Run Research Backtests.bat` occasionally (e.g. yearly) on new data.
  Do not re-tune the strategies on the same holdout period.
* Package versions are pinned in `requirements.txt`. Upgrading them is a
  deliberate step: run the tests after any upgrade.
