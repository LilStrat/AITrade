# Strategies

Two deliberately simple, well-known strategies. They were chosen because their
rules are explicit, they suit end-of-day data (the only affordable data), and
there is published research on why they *might* work. They also tend to fail in
different market conditions. Neither is assumed to be profitable.

All strategies share the same timing contract: **decide after the close using
data up to that close; execute at the next session's open.** Indicators are
causal (tests prove that changing future prices never changes past values).

A broad-market **regime filter** applies to both: new long entries only when the
benchmark (ISF.L, the iShares FTSE 100 ETF) closes above its 200-day average.

---

## 1. Trend Pullback (`trend_pullback`)

| | |
|---|---|
| **Behaviour exploited** | Short-term mean reversion inside long-term uptrends. Liquid shares and index ETFs have historically tended to bounce after a few sharp down days (e.g. Connors & Alvarez, *Short Term Trading Strategies That Work*, 2008, mostly US data). The effect has weakened as it became well known. |
| **Instruments / timeframe** | Liquid LSE large caps and broad ETFs; daily bars; 1-10 sessions |
| **Entry (all at the close)** | close > 200-day SMA; 50-day SMA > 200-day SMA; RSI(2) < 10; 5-day change better than -12%; ATR(14) < 5% of price; regime risk-on. Buy at the next open. |
| **Exit** | Next open after any close where: close > 5-day SMA (bounce achieved); close < 200-day SMA (trend broken); or 10 sessions held. |
| **Stop / profit taking** | Initial stop = fill − 2.5 × ATR(14), resting, checked against each day's low; gap below = fill at the (worse) open. Profit taken by the 5-day-SMA rule; no fixed target. |
| **Invalidation** | Downtrend, risk-off regime, crash-like fall (often news), extreme volatility. |
| **Risks** | Falling knives; overnight gaps; many small wins can be wiped out by a few big losses. **Costs:** high turnover makes 0.5% stamp duty on UK shares very expensive. ETFs avoid stamp duty. |
| **Parameters (range)** | trend_sma 200 (100-300) · mid_sma 50 · rsi_len 2 · rsi_entry 10 (2-40) · exit_sma 5 · stop_atr 2.5 (1-5) · max_hold 10 · crash_limit 12% · max_atr 5% |

## 2. Breakout Momentum (`breakout_momentum`)

| | |
|---|---|
| **Behaviour exploited** | Time-series momentum / trend following: rising assets tend to keep rising for a while (e.g. Moskowitz, Ooi & Pedersen, *Time Series Momentum*, 2012; Donchian channel breakouts). Volume confirmation filters low-participation breakouts. |
| **Instruments / timeframe** | Liquid LSE shares and ETFs; daily bars; days to months |
| **Entry (all at the close)** | close > highest close of the previous 55 days; volume ≥ 1.5 × previous 20-day average; close > 200-day SMA; 126-day return > 0; ATR(14) < 6%; close ≤ 12% above its 20-day SMA; regime risk-on. Buy at the next open. |
| **Exit** | Next open after a close below the previous 20-day low, or after 60 sessions. |
| **Stop / profit taking** | Initial stop = fill − 2 × ATR(14); then a trailing "chandelier" stop = highest close since entry − 3 × ATR(14), only ever raised. Profits are taken by the trailing stop. |
| **Invalidation** | Risk-off regime, negative 6-month momentum, low-volume breakout, over-extended price. |
| **Risks** | Frequent false breakouts (many small losses); profits rely on a few big trends; poor in sideways markets; win rate usually below 50% by design. |
| **Parameters (range)** | breakout_len 55 (10-250) · volume_mult 1.5 · trend_sma 200 · momentum_len 126 · stop_atr 2 · trail_atr 3 (1.5-6) · exit_low_len 20 · max_hold 60 · max_atr 6% · max_extension 12% |

---

## Evidence so far - be clear about this

**No real-market evidence exists yet.** The build environment had no access to
market data, so every backtest run during development used **synthetic random
prices**. Those runs only prove the machinery works: costs, fills, stops,
accounting, lookahead protection. They say **nothing** about whether either
strategy makes money.

One synthetic result is still informative about **costs**. Over ~11 years of
random prices on ~50 instruments, the two strategies made about **+14% before
costs and −48% after costs**: 703 trades paying £6,270 in commission, stamp
duty and spread on £10,000. On data with no edge, short-term trading of UK
shares at this account size loses heavily to costs. As a result the risk
manager now rejects trades whose round-trip costs exceed 20% of the amount at
risk.

The first genuine evidence comes from **`Run Research Backtests.bat`** on your PC
(see `SETUP.md` step 6).

## Research protocol (fixed before seeing any real results)

`tradebot/backtesting/research.py`:

1. Split the history: **development = first 75%**, **holdout = last 25%**, never
   used for any choice.
2. Inside development: rolling **walk-forward** windows, 3 years of training then
   1 year of testing, stepping 1 year. Each window picks the best of a small,
   pre-registered parameter grid on the training years (return ÷ drawdown,
   minimum 10 trades) and runs it on the unseen test year. The chained test
   years give an out-of-sample record.
3. **Sensitivity table**: every grid point over the whole development period.
   If only one lucky setting works, that is a warning sign.
4. Holdout: run **once** with the defaults and once with the parameters chosen on
   development data.
5. **Verdict** (pre-registered criteria, on the holdout with chosen parameters):
   * fewer than 20 trades → **INSUFFICIENT EVIDENCE**
   * profit factor < 1.10, or return not above cash (4%/year), or walk-forward
     out-of-sample return ≤ 0 → **REJECT**
   * otherwise → **PROVISIONAL**: qualifies for extended paper trading only.

The report also states whether the strategy beat simply holding the benchmark,
and warns about multiple-testing bias. Running research repeatedly after
tweaking rules quietly turns the holdout into in-sample data, so treat any
second look at the same period with suspicion.

## Ideas not implemented (and why)

* MACD and Bollinger Bands: implemented as tested indicators, but not used in a
  strategy. Adding more indicators without a reason mostly adds overfitting.
* Intraday strategies: need paid real-time data and intraday bars. Not credible
  with free end-of-day data.
* Short selling, leverage, options, CFDs, leveraged ETFs: excluded by
  requirement (locked in code).
