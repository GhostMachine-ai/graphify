# Risks and limitations

## The strategies do not beat doing nothing

On 630 real daily bars the book returned **+3.26%** while equal-weight
buy-and-hold on the same five instruments returned **+60.75%** — a 57-point
shortfall, with a 10% drawdown taken along the way. Profit factor 1.09 and
Sharpe +0.24 over a single window are noise-level, not evidence of edge.

A verified engine and a profitable strategy are different things. This repository
has the first and not the second.

### An earlier version of this file reported −5.89%

That number was an artifact of a bug in this engine, not a property of the data.
The trailing stop was ratcheted using a bar's own high and then tested against
that same bar's low, which silently assumes the high occurred before the low —
something OHLC does not record. Correcting the ordering moved the result by 9.15
percentage points and flipped its sign.

The full 192-test suite passed under **both** orderings, because the components
were tested in isolation and never in composition. It was found by an independent
verification pass. `tests/test_intrabar_stop_ordering.py` now pins the ordering
with a golden test over the committed bars, verified to fail if reverted. See the
README for the worked example.

## What the risk controls do not protect against

- **Overnight and weekend gaps — the largest controllable risk in the book.**
  Stops are checked against bar extremes and a gap through the stop fills at the
  open, which is modelled, but a gap can exceed the intended 1-ATR loss by an
  unbounded amount. In the real run 17 of 140 trades exited via
  `stop_gap_through_open` for −$10,392: an average of −$611 against −$173 for an
  ordinary stop-out, i.e. **3.5× worse**. Three trades lost 1.08×–1.32× the
  ex-ante 1% risk budget this way. ATR sizing does nothing about this; only
  position limits or avoiding overnight exposure would.
- **A sticky drawdown halt that made the result worse.** Once the 10% limit trips
  the run stops permanently and does not resume on recovery. On this data that
  converted **+3.26% into −5.06%**: the breaker liquidated at the first 10%
  drawdown and locked in a loss the book would otherwise have recovered, and cut
  the sample from 140 trades to 88. A circuit breaker is a risk control, not a
  neutral observer — it changes both the result and what the backtest measures.
- **Correlation beyond the one hardcoded pair.** The filter blocks a BTC long
  while SPY and QQQ are both long. It knows nothing about GLD/USO co-movement, or
  about correlations that only appear in a crisis.
- **Ex-ante risk below target, which is not the same as realised risk.** The
  notional cap usually binds before the ATR target, so the *planned* per-trade
  risk is under 1% (108 of 140 sizings were cap-bound). Realised losses can still
  exceed it through gaps, as above. The invariant that holds is about the sizing
  decision, not about outcomes.
- **Single-venue, single-currency, no financing.** No borrow costs on shorts, no
  margin interest, no dividend or carry adjustments beyond split-adjusted prices.

## Backtest-specific caveats

- **Survivorship and selection.** Five instruments chosen in advance, all of which
  existed and traded throughout the window. IBIT launched in January 2024, so the
  window starts near its inception.
- **One window, one regime.** 2024-04 to 2026-10 trended strongly upward for all
  five instruments. Mean-reversion logic is structurally disadvantaged in such a
  regime, and a different window could invert the conclusion. One backtest over
  one period is an anecdote.
- **No parameter search, deliberately.** Band widths, ATR multiples and EMA
  lengths come from the specification, not from fitting. Tuning them until the
  result improves would produce a curve-fitted number that says nothing about the
  future, so it was not done. Note that this also means +3.26% is an *untuned*
  result, which cuts both ways.
- **Fills are optimistic in one respect.** Market orders are assumed to fill at
  the open plus slippage, with unlimited liquidity and no partial fills.
- **Intra-bar path is unknowable, and the engine now refuses to guess.** After the
  stop-ordering fix, a stop is tested against the level in force before the bar
  opened. That is the conservative reading; the opposite assumption produced a
  materially different answer, which is itself a warning about how much
  bar-resolution backtests depend on assumptions the data cannot settle.

## Things that would have to change before this went near real money

1. A strategy book with demonstrated out-of-sample edge. +3.26% on one untuned
   window against a +60.75% benchmark is not that.
2. Walk-forward or cross-regime validation, not a single window.
3. A gap-risk policy. It is the dominant loss channel and nothing currently
   addresses it.
4. Live order plumbing, reconciliation and position-state recovery — none of which
   exists here, by design.
5. An independent kill path that does not depend on this process being healthy.
   The `KILL` file is a start precisely because any shell can create it.
