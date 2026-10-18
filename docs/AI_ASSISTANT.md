# Local AI (Ollama + Llama 3.1 8B)

## What it is used for
* Explaining a signal in plain English (why it qualified, what the risk
  manager decided, why it could fail)
* Summarising market conditions from **calculated** facts (regime, breadth,
  best/worst performers, data warnings)
* Optional commentary appended to daily/weekly reports, in a labelled section

## What it is NOT used for
Prices, indicators, signals, sizing, risk checks, orders, accounting. All of
these are deterministic, tested code. The AI module imports nothing from the
broker, core, risk or portfolio code (a test checks this). It only returns
text, which is stored separately and labelled "AI-generated interpretation".
If the text contains numbers that do not appear in the facts it was given,
the dashboard flags them as unreliable.

News summarisation is **not** included: there is no free, licensed news feed
that can be used reliably. Without real input, the model would only invent
plausible-sounding text.

## Your hardware
* Llama 3.1 8B at Q4_K_M is roughly 4.9 GB of weights. With a 4,096-token
  context it fits within the RTX 5060's 8 GB of VRAM, leaving headroom, and
  16 GB of system RAM is enough. An upgrade is not needed for this use.
* `keep_alive: 5m` tells Ollama to unload the model 5 minutes after the last
  request, freeing GPU memory. The model is only loaded when you click an AI
  button.
* Weaknesses of an 8B model: arithmetic and precise recall are unreliable, and
  it can state things confidently that are wrong. That is why it only ever
  rephrases verified facts.
* If Ollama is not running, the AI page says so and everything else works
  normally.
