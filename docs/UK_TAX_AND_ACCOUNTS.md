# UK accounts, tax records and regulation - general information

**This is general information, not tax or legal advice.** Rules change and
depend on your circumstances. Figures were checked on 18 October 2026, mostly
via secondary sources (gov.uk could not be opened from the build environment),
so confirm on GOV.UK before relying on them.

## Legal requirements vs guidance

| | Status |
|---|---|
| Report and pay CGT on taxable gains outside an ISA | **Legal requirement** |
| Keep records of purchases, disposals, costs and dividends | **Legal requirement** (HMRC expects records to support your tax return) |
| Market abuse rules (no insider dealing, no market manipulation) | **Legal requirement for everyone**, including private individuals and bots |
| Trading your own money through a regulated broker | No FCA authorisation is needed for a private individual. FCA rules for firms do not apply to you personally. |
| Your broker's terms on automated trading / API use | **Contractual**: read them before going live |
| Using a GIA vs an ISA | Your choice; see below |

## GIA vs Stocks & Shares ISA

| | General Investment Account (GIA) | Stocks & Shares ISA |
|---|---|---|
| Capital gains | Taxable above the annual exempt amount (**£3,000** in 2026-27); rates **18% / 24%** depending on income 🟡 | Free of UK CGT |
| Dividends | Taxable above the dividend allowance (£500) 🟡 | Free of UK dividend tax |
| Contribution limit | None | **£20,000** per tax year (2026-27). From 6 April 2027 the cash ISA limit for under-65s falls to £12,000; the overall limit stays £20,000 🟡 |
| Losses | Can offset gains (must be reported) | Cannot be used |
| Stamp duty on UK shares | 0.5% | 0.5% (an ISA does **not** exempt you) |
| API automation | Widely supported (e.g. IBKR) | Trading 212 API supports ISA; IBKR ISA mentions API access (unconfirmed details) |
| Fees | Broker dependent | e.g. IBKR ISA £3/month minimum activity fee |

Considerations: if the strategies turn out to be worth running, an ISA's tax
shelter is valuable, but losses inside an ISA cannot offset other gains. An ISA
also limits how much you can add each year. I found no official statement on
how HMRC treats very frequent trading inside an ISA. If you plan to trade very
frequently, take advice. **Decide this after paper trading, not before.**

## Share matching rules (implemented as an ESTIMATE)

For disposals outside an ISA, HMRC matches shares sold with shares bought in
this order:
1. **same day** - acquisitions on the same day
2. **30-day rule** ("bed and breakfasting") - acquisitions in the next 30 days
3. **Section 104 pool** - the average cost of all other holdings

`tradebot/portfolio/tax.py` applies these rules to the paper account. The
History page and CSV exports show per-disposal matches and per-tax-year totals
(6 April to 5 April), with the annual exempt amount for comparison.

**What it does not do:** decide your tax liability, include gains or losses from
elsewhere, handle accumulation-ETF "notional distributions" or offshore
reporting-fund excess income, or treat splits inside a 30-day window exactly.
Reporting can be required even when no tax is due, for example when disposal
proceeds exceed £50,000 (from 2023-24). Check GOV.UK.

## Records the bot keeps (exportable to CSV)

Every order, order-status change, fill (price, quantity, time, commission,
stamp duty, levies, FX cost, spread/slippage estimate), dividend, split,
correction and daily valuation. The ledger is append-only: the database itself
refuses edits or deletions, so corrections appear as separate, explained
entries. For a real account, your **broker's statements are the authoritative
record**. The bot's records are a reconciliation aid.

## Sources
* HMRC ISA guidance: https://www.gov.uk/guidance/stocks-and-shares-investments-for-isa-managers
* Tax when you sell shares: https://www.gov.uk/tax-sell-shares/what-you-pay-it-on
* ISA allowance 2026-27 and 2027 changes (secondary): https://www.cmcmarkets.com/en-gb/investing-guides/isa-allowance , https://monevator.com/annual-isa-allowance/
* CGT allowance/rates 2026-27 (secondary): https://www.fiduciawealth.co.uk/2026/03/the-capital-gains-tax-essentials-you-need-to-know/
* Stamp duty on shares: https://www.gov.uk/hmrc-internal-manuals/stamp-taxes-shares-manual/stsm014010
