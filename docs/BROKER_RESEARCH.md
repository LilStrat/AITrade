# Broker & market-data research (UK resident, stocks and ETFs)

Researched 18 October 2026. **Read the caveats.** Some official sites
(interactivebrokers.co.uk, docs.trading212.com, gov.uk) could not be opened
directly from the build environment, so several facts come from search-result
extracts of official pages. Each one is marked with how well it was verified.
Fees and terms change: check the provider's current pages before opening an
account. Nothing was purchased, subscribed to or signed up for.

Verification key: ✅ official source seen · 🟡 official page via search extract or
reputable secondary source · ❓ could not verify.

## 1. Comparison

| | **Interactive Brokers UK** | **Trading 212** | **Saxo UK** | **IG** | **Alpaca** |
|---|---|---|---|---|---|
| UK residents | Yes - IBKR (U.K.) Ltd 🟡 | Yes 🟡 | Yes 🟡 | Yes 🟡 | Unclear for direct UK retail. Acquired WealthKernel (Alpaca Europe, Apr 2026) 🟡 |
| API for a personal bot | TWS API (needs TWS/IB Gateway running) and Client Portal Web API; mature, official ✅🟡 | "Public API" **v0, beta**; key-based; demo + live 🟡 | OpenAPI; live use may need Saxo evaluation/approval 🟡 | REST API exists, but I found no documentation that it supports **share dealing/ISA** accounts ❓ | Excellent API, but UK retail access unconfirmed ❓ |
| LSE stocks & ETFs | Yes, plus most global exchanges 🟡 | Yes (large LSE/US range) 🟡 | Yes 🟡 | Yes (web/app) 🟡 | US equities; EU equities via Alpaca Europe 🟡 |
| Automated orders allowed | Yes - the API is offered for automation 🟡 | Yes via API (beta) 🟡 | Yes (after approval for live) 🟡 | ❓ for shares | Yes |
| Paper/demo | Paper account, **but needs a fully open and funded IBKR Pro live account** 🟡 | Free demo environment with its own API URL 🟡 | Developer simulation environment ❓(not re-verified) | Demo for CFD/spread bets ❓ | Paper trading free ✅ (well known) |
| Market data via API | Delayed data free; real-time LSE needs a paid subscription (UK monthly price not found; snapshot $0.03/quote) 🟡 | No historical-price/candle endpoint found ❓ | Delayed by default; real-time paid ❓ | n/a | US data |
| Order types via API | Full (market, limit, **resting stops held at the broker**) 🟡 | **Live: market orders only** per docs; no resting stop in live 🟡 | Full 🟡 | n/a | Full |
| UK share commission | Fixed plan £3 or 0.05% above £6,000 (+ pass-through on tiered) 🟡 | £0 commission 🟡 | Varies by tier ❓ | £8 GIA / £3 ISA online UK shares 🟡 | n/a |
| FX conversion | ~0.03% auto-convert; 0.002% manual (min $2) 🟡 | 0.15% 🟡 | ❓ | ❓ | n/a |
| ISA via API | IBKR ISA page says TWS & APIs are available to ISA clients - specifics unconfirmed 🟡 | **Yes: API works for Invest and Stocks ISA accounts** 🟡 | ❓ | ISA exists, API unclear ❓ | ISA via Alpaca Europe infrastructure (B2B) ❓ |
| Other fees | ISA: £3/month minimum activity fee (offset by commissions); GIA: no inactivity fee 🟡 | none noted | ❓ | ❓ | n/a |
| Reliability / maintenance | Robust, but TWS/Gateway must run, re-login daily/weekly, 2FA | Simple key; **beta** - endpoints may change; market order endpoint reported **not idempotent** 🟡 | Token refresh; approval step | - | - |
| Beginner difficulty | Hard | Easy | Medium | - | - |

UK-specific constraints found during research:

* **US-domiciled ETFs** (e.g. SPY, QQQ) are generally not available to UK retail
  clients because they lack a UK/EU key information document; brokers such as
  IBKR block them. Use UCITS ETFs listed in London (e.g. VUSA, CSP1, EQQQ). The
  FCA's new Consumer Composite Investments regime (from April 2026) replaces
  the KID with a "Product Summary"; whether that changes access to US ETFs was
  not confirmed. 🟡
* **Stamp Duty Reserve Tax 0.5%** applies to purchases of UK shares (also inside
  ISAs); UK-listed ETFs and US shares are exempt. A relief exists for some
  newly listed companies (3 years from listing, announced Nov 2025). Sources
  disagree about AIM shares - the bot does not trade AIM by default. 🟡
* **PTM levy** (£1.50 on UK share trades over £10,000): included in the cost
  model from prior knowledge; **not re-verified** this session ❓.

## 2. Market data options

| Source | Cost | LSE coverage | Real-time? | Licence | Used in v1 |
|---|---|---|---|---|---|
| Yahoo Finance via `yfinance` | Free | Daily bars, dividends, splits | No (end of day) | Personal use only (per the project); unofficial, no SLA 🟡 | **Default** |
| Stooq CSV | Free | Daily bars | No | Personal use; currency/adjustment not documented | Fallback |
| EODHD | Free 20 calls/day; "EOD Historical All-World" about **$19.99/month** 🟡 | Advertised 60+ exchanges (check LSE list before buying) | End of day | Paid licence, personal use | Optional adapter |
| IBKR | Delayed free; real-time paid | Excellent | Yes (paid) | Broker terms | Future |

**There is no free, licensed, real-time LSE feed suitable for this project.** The
bot is therefore designed around **end-of-day decisions**: it decides after the
close and acts at the next open. This is honest about the data: it never
pretends delayed data is real time, and the dashboard labels the source and
delay on every page.

## 3. Recommendation

**Now (stages 1-10): no broker account is needed.**
* Local paper trading on this PC with free end-of-day data (Yahoo, Stooq
  fallback). **Cost: £0.**
* If Yahoo becomes unreliable, switch the data provider to EODHD (about $20/month)
  in Settings. Nothing else changes.

**Later, only if paper trading justifies it (stage 11):**
* **Interactive Brokers UK (General Investment Account)** is my primary
  recommendation for automated live trading. It has the most complete official
  API, supports **real stop orders held at the broker**, so your protection
  still works if your PC or internet is off, has a paper account for broker-side
  testing, supports GBP, and has low costs at the sizes involved. The downsides
  are the learning curve, the need to run IB Gateway, and the need to fund the
  account before the paper account and API work.
* **Trading 212 (Invest or Stocks ISA)** is a credible simpler alternative,
  especially for an ISA. It has zero commission, an easy API key and a free demo.
  But the API is a **beta**, supports only **market orders in live trading**, so
  stop-losses would have to be simulated by the bot while your PC is running,
  and its order endpoint is reported as not idempotent. The bot's
  duplicate-protection design already accounts for that.
* GIA vs ISA: see `UK_TAX_AND_ACCOUNTS.md`. I do not recommend either until
  paper trading shows the strategies are worth running.

## 4. Expected costs

| Item | Now | Later (optional) |
|---|---|---|
| Software, data, dashboard | £0 | £0 |
| Market data | £0 (Yahoo/Stooq) | EODHD ~$20/month if needed; IBKR real-time LSE subscription (price unverified) |
| Broker | none | IBKR: £3 or 0.05% per UK trade + 0.5% stamp duty on UK shares; ISA £3/month minimum activity fee. T212: £0 commission, 0.15% FX |
| Local AI | £0 (your Ollama + Llama 3.1 8B) | £0 |
| Electricity | PC running while the app is open | same |

## Sources

* IBKR API page (requested): https://www.interactivebrokers.co.uk/en/trading/ib-api.php (could not be opened from the build environment; found via search: https://www.interactivebrokers.co.uk/de/trading/ib-api.php)
* IBKR Web API - trading access for individuals (paper requires funded IBKR Pro): https://www.interactivebrokers.com/docs/web-api/trading/getting-started/trading-access-for-individuals
* IBKR paper credentials: https://www.interactivebrokers.com/docs/general/market-data-subscriptions/market-data-users/finding-your-paper-user-credentials/step-five-review-your-paper-credentials
* IBKR market data subscriptions (paper accounts cannot subscribe): https://www.interactivebrokers.com/campus/ibkr-api-page/market-data-subscriptions/
* IBKR market data pricing / snapshot fees: https://www.interactivebrokers.com/en/index.php?f=14193 , https://ibkb.interactivebrokers.com/tag/market-data?page=1
* IBKR order-type notes (paper is simulated): https://www.interactivebrokers.com/docs/general/order-types/notes-limitations/introduction
* IBKR LSE fees: https://www.interactivebrokers.co.uk/en/accounts/fees/LSEstkfees.php ; European commissions: https://www.interactivebrokers.co.uk/fr/index.php?f=49592
* IBKR UK ISA (TWS & APIs mentioned): https://www.interactivebrokers.co.uk/fr/accounts/isa-accounts.php ; required minimums (ISA £3 activity fee): https://www.interactivebrokers.co.uk/en/accounts/required-minimums.php
* IBKR PRIIPs/KID restriction: https://ibkb.interactivebrokers.com/node/2993
* Trading 212 Public API docs: https://docs.trading212.com , https://docs.trading212.com/api/orders , https://docs.trading212.com/api/section/general-information/key-concepts
* Saxo OpenAPI: https://www.developer.saxo/openapi/learn/writing-an-openapi-application , https://home.saxo/en-gb/platforms/api
* IG share dealing & ISA: https://ig.com/uk/share-trading-stockbrokers , https://ig.com/uk/investments/isa/share-dealing
* Alpaca Europe / WealthKernel: https://alpaca.markets/blog/alpaca-enters-uk-and-eu-market-through-wealthkernel-acquisition/ ; eligibility: https://alpaca.markets/learn/account-eligibility/
* EODHD pricing: https://eodhd.com/pricing ; overview: https://nexusfi.com/d/data-providers/eodhd-eod-historical-data/
* yfinance (personal use statement): https://pypi.org/project/yfinance/
* Stamp duty: https://www.gov.uk/hmrc-internal-manuals/stamp-taxes-shares-manual/stsm014010 , https://www.gov.uk/government/publications/stamp-duty-reserve-tax-relief-changes , https://help.freetrade.io/en/articles/1820186-what-s-stamp-duty
* CCI regime: https://uktaxdrag.co.uk/ucits-vs-us-domiciled-etfs-uk-2026-27
