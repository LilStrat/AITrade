# Live trading - NOT enabled

v1 contains **no live broker adapter**. `live` and `broker_paper` modes are
refused in code (`tradebot/brokers/live_guard.py`), and tests verify that no
order can be created in those modes. Nothing in this project can withdraw,
deposit or transfer money.

Live trading will only be considered when **you explicitly ask for it** (stage 11).
At that point I will produce a separate readiness report covering the items
below. Nothing will be switched on automatically.

## Readiness checklist (all required)

1. [ ] Research reports on real data show at least one strategy as **PROVISIONAL**
       (not REJECT / INSUFFICIENT EVIDENCE).
2. [ ] At least **3 months** of local paper trading with the same settings, and
       the results reviewed against the backtest: trade frequency, costs,
       slippage, drawdowns, number of gap stops.
3. [ ] Zero unexplained accounting discrepancies or reconciliation alerts in
       that period.
4. [ ] Full test suite passing on your PC.
5. [ ] Broker chosen and account opened **by you**, with API terms read
       (see `BROKER_RESEARCH.md`). Broker-side paper account tested first.
6. [ ] Broker adapter implemented with: idempotent client order IDs, lookup
       before any re-submission, cancellation handling, timeouts, reconciliation
       of positions/cash/orders against the broker **before every cycle**, and
       resting stop orders held at the broker.
7. [ ] Small initial capital you can afford to lose entirely; position limits
       reviewed.
8. [ ] Credentials stored only via the broker's official login and the local
       `.env` file, never in code, chat or screenshots.
9. [ ] Your explicit written approval, plus the confirmation phrase
       `TRADEBOT_LIVE_AUTHORISED=I UNDERSTAND THIS USES REAL MONEY` set by you in
       `.env`.
