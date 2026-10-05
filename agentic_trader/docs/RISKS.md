# Risks and limitations

## The strategies do not work on the data tested

The headline finding, repeated here because it is the most important fact about
this repository: on 630 real daily bars the book returned **−5.89%** while
equal-weight buy-and-hold on the same instruments returned **+60.75%**. Every
strategy and every symbol lost money. See the README for the breakdown.

A verified engine and a profitable strategy are different things. This has the
first and not the second.

## What the risk controls do not protect against

- **Overnight and weekend gaps.** Stops are checked against bar extremes; a gap
  through the stop fills at the open, which is modelled, but a large gap can still
  exceed the intended 1-ATR loss by an unbounded amount. 29 of 144 trades in the
  real run exited via `stop_gap_through_open`, for −$5,393.
- **Correlation beyond the one hardcoded pair.** The filter blocks a BTC long
  while SPY and QQQ are both long. It knows nothing about GLD/USO co-movement, or
  about correlations that appear only in a crisis.
- **Realised risk below target.** The notional cap usually binds before the ATR
  target, so per-trade risk is *under* 1%. Good for safety, but it means the
  stated risk budget is not the one actually taken.
- **A sticky drawdown halt.** Once the 10% limit trips, the run stops permanently
  and does not resume on recovery. In the real run this cut the sample from 144
  trades to 70 — the halt is a risk control, not a neutral observer, and it
  changes what the backtest measures.
- **Single-venue, single-currency, no financing.** No borrow costs on shorts, no
  margin interest, no dividend or carry adjustments beyond split-adjusted prices.

## Backtest-specific caveats

- **Survivorship and selection.** Five instruments chosen in advance, all of which
  existed and traded throughout the window. IBIT in particular launched in January
  2024, so the window starts near its inception.
- **One window, one regime.** 2024-04 to 2026-10 was strongly trending upward for
  all five instruments. Mean-reversion logic is structurally disadvantaged in such
  a regime, and a different window could invert the conclusion. One backtest over
  one period is an anecdote.
- **No parameter search, which is deliberate.** Band widths, ATR multiples and EMA
  lengths come from the specification, not from fitting. Tuning them until the
  result turns positive would produce a curve-fitted number that says nothing
  about the future, so it was not done.
- **Fills are optimistic in one respect.** Market orders are assumed to fill at
  the open plus slippage, with unlimited liquidity and no partial fills.

## Things that would have to change before this went anywhere near real money

1. A strategy book with demonstrated out-of-sample edge. There isn't one yet.
2. Walk-forward or cross-regime validation, not a single window.
3. Live order plumbing, reconciliation, and position-state recovery — none of
   which exists here, by design.
4. An independent kill path that does not depend on this process being healthy.
   The `KILL` file is a start precisely because any shell can create it.
