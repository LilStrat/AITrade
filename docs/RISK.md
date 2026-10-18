# Risk management

The risk manager (`tradebot/risk/manager.py`) is independent of the strategies
and the AI: it imports neither, and no strategy or AI output can bypass it.
Every proposed entry is **sized and checked** here. A rejection is final for
that signal, and every check, passed or failed, is shown on the Opportunities
page.

## Default limits (examples, not advice)

| Setting | Default | What it means | If you raise it… |
|---|---|---|---|
| Max risk per trade | 0.5% of equity | Size = (0.5% × equity) ÷ (entry − stop) | Bigger positions; losing streaks hurt more |
| Daily loss limit | 1% | No new entries for the rest of that day | Bad days can compound |
| Drawdown pause | 5% from peak | **All** new entries stop until you review and press Resume | Deeper losses before a forced review |
| Max single position | 20% of equity | Caps concentration | One company can dominate results |
| Max total invested | 95% (hard cap 100%) | No borrowing ever | n/a (leverage is locked off) |
| Max per sector | 40% | Limits sector bets | Correlated losses |
| Max open positions | 6 | | More capital spread thinner |
| Correlated holdings | max 2 above 0.80 correlation (60 days) | Avoids several bets on one move | |
| Cash reserve | 3% | Never invested | |
| Size vs volume | 1% of 20-day average volume | Realistic fills | Fill assumptions become unrealistic |
| Costs vs risk | reject if round-trip costs > 20% of the amount at risk | Stops trades that fixed fees and stamp duty make pointless | More cost drag |
| Stop distance | 1%-20% from entry | Rejects nonsensical stops | |
| Failure pause | 3 consecutive failed daily cycles | Trading paused until you resume | |
| Short selling / leverage | off - **locked** | The code refuses to start if enabled | n/a |

Protected settings can only be changed on the Settings page after ticking a
confirmation box. Values outside safe bounds are refused, every change is
logged on the Settings page, and the previous settings file is backed up.

## Other safeguards

* **Stale or suspicious data** blocks new orders for that instrument. Stale
  benchmark data blocks all new entries.
* **Emergency stop** button: cancels pending entries and blocks all new orders.
  Protective stops are kept, because they reduce risk.
* **Duplicate protection**: one position per instrument; deterministic order IDs
  (account:symbol:purpose:date). Submitting the same ID twice returns the
  existing order.
* **Reconciliation before decisions**: negative cash, sells larger than holdings
  or positions without stop records mark the account "uncertain", which blocks
  all new orders and raises a critical alert.
* **Catch-up after downtime** settles resting orders and stops for missed days,
  but never creates back-dated entries.
* **No martingale or averaging down.** Sizing depends only on equity and stop
  distance, never on recent losses.

## Stop-losses do NOT guarantee a maximum loss

If a price **gaps** below your stop (for example after overnight news), the
simulated fill is at the opening price, which is worse than the stop. Real
markets behave the same way. Slippage and thin trading can make losses larger
too. So "0.5% risk per trade" is the loss under normal conditions, not a cap.
The backtest report counts how many stop exits were gap fills.
