# agentic_trader

A multi-asset **backtest and research** framework: three strategies, ATR-based risk
sizing, a cross-asset correlation filter, a portfolio drawdown breaker, and a
fail-closed conviction gate between research and execution.

## What this is not

- **Not investment advice**, and it makes no claim of profitability.
- **Not wired to a broker.** It submits no orders, holds no credentials, and
  contains no order-placement code. The nearest thing to execution is a simulated
  fill in `agent_trader/execution.py`.
- **Not profitable on the data tested.** See the honest result below.

## The result, stated plainly

On 630 days of real daily bars (2024-04 to 2026-10) for SPY, QQQ, GLD, USO and
IBIT, pulled from the RobinHood MCP connector:

| | Return | Trades | Win rate | Profit factor | Sharpe | Max DD |
|---|---|---|---|---|---|---|
| Strategy book, breaker off | **+3.26%** | 140 | 44.3% | 1.09 | +0.24 | 10.44% |
| Strategy book, 10% breaker | **−5.06%** | 88 | 36.4% | 0.80 | −0.39 | 10.18% (halted) |
| Equal-weight buy & hold | **+60.75%** | — | — | — | — | — |

**The book is roughly flat, and it loses to buy-and-hold by 57 percentage
points.** Holding the same five instruments over the same window returned
+60.75%; trading them with this logic returned +3.26% while taking a 10% drawdown
to do it.

A profit factor of 1.09 and a Sharpe of +0.24 over a single window are
noise-level, not evidence of edge. The honest summary is that this book does not
beat doing nothing, and costs real risk to break roughly even.

Two findings worth more than the headline:

**The 10% drawdown breaker makes the outcome worse, not better.** It halts the run
at the first 10% drawdown and liquidates, locking in −5.06%, where letting the
book run recovers to +3.26%. A circuit breaker is a risk control, not a neutral
observer — it changes what the backtest measures, and on this data it converted a
breakeven result into a loss.

**Gap risk dominates the loss side.** Of 140 trades:

```
  72 trades  −$12,421  stop_hit                 (−$173 average)
  50 trades  +$25,868  reverted_to_mean         (+$517 average)
  17 trades  −$10,392  stop_gap_through_open    (−$611 average)
```

Only 17 trades exited through a gap, but they averaged 3.5× the loss of an
ordinary stop-out — an overnight gap turns a stop into a market order at whatever
price opens. That is the single largest controllable risk in the book, and no
amount of ATR sizing addresses it.

Fees were $528 against gross PnL of $3,784, so costs are 16% of the profit but not
the story.

### A correction, and why it matters

**An earlier version of this README reported −5.89%.** That figure was wrong, and
the cause was a bug in this engine, not in the data.

The trailing stop was being ratcheted using a bar's own high and then tested
against that same bar's low — which silently assumes the high occurred before the
low, something OHLC does not record. For `O=109 H=110 L=99` on a long stopped at
98 with a 2-ATR trail, the old code lifted the stop to 108 and then "stopped out"
at 108, booking a **+800 profit and labelling it a stop exit**. A trailing stop
cannot fill above its own trail level; that was the tell.

Correcting the ordering moved the result by **9.15 percentage points and flipped
its sign**. Every number above is post-fix.

The failure that let it through is worth recording: the entire 192-test suite
passed under *either* ordering. `test_execution.py` tested `update_extreme` and
`stop_hit` in isolation, `test_risk.py` tested `trail_stop` in isolation, and
nothing tested their composition inside the engine loop. It was found by an
independent verification pass, not by the tests. `tests/test_intrabar_stop_ordering.py`
now pins the ordering with a golden test over the committed bars, and that test is
verified to fail if the ordering is reverted.

## Quick start

```bash
python3 -m agent_trader cache-status                                  # what data is cached
python3 -m agent_trader backtest --feed cached --conviction 0.85      # real bars
python3 -m agent_trader backtest --feed cached --conviction 0.20      # gate veto: 0 trades
python3 -m agent_trader gate-demo                                     # fail-closed matrix
python3 -m unittest discover -s tests -t .                            # 192 tests
```

No dependencies: standard library only, Python 3.10+.

## Design

Five layers, each testable alone:

| Module | Responsibility |
|---|---|
| `indicators.py` | SMA, EMA, stdev, ATR, Bollinger, Donchian, RSI, volume surge |
| `strategy.py` | direction and timing only — never size, never veto |
| `risk.py` | position sizing, correlation filter, drawdown breaker, KILL switch |
| `gate.py` | fail-closed conviction veto over a sidecar JSON ledger |
| `execution.py` | slippage, fees, stop resolution including gaps |
| `backtest.py` | chronological loop with a fixed intra-bar ordering |
| `metrics.py` / `benchmark.py` | performance, and the benchmark it must beat |

### Three things worth knowing

**The gate fails closed.** Missing signal, stale signal, future-stamped signal,
non-numeric conviction, corrupt ledger, kill switch, *or any raised exception* all
deny the trade. Most harnesses default a broken confirmation hook to *permit*;
this one does the opposite, because the moment something unexpected happens is
exactly when you want trading stopped. Verified on real data: the same 630 bars at
conviction 0.20 produce **0 trades and exactly $100,000.00 of untouched equity**.

**Position sizing has two constraints and the tighter one wins.** The ATR target
(`equity × 1% / (ATR × mult)`) and a notional cap both apply. For low-volatility
instruments the ATR target demands leverage — GLD at ATR 0.80 needs ~$237k of
exposure to risk $1k on a 1-ATR stop — so the cap binds and realised risk is
*below* 1%. In the real run the cap bound on 50 of 70 entries. The engine records
which constraint bound rather than claiming a flat 1%.

**Annualisation is inferred from the data.** Marks per elapsed calendar year, not
a hardcoded 252. Using a median bar gap would read a year of SPY 15-minute bars as
35,064 periods instead of ~6,552 and inflate Sharpe by about 2.3×.

## Data

Market data reaches this project through MCP connectors available to an *agent*,
not to this library. The boundary is explicit and on disk:

```
agent (MCP tools) → data/cache/<SYMBOL>_<interval>.json → CachedFeed → engine
```

`scripts/ingest_robinhood.py` converts a saved MCP historicals response into that
cache. See [docs/DATA.md](docs/DATA.md) for the schema, the interval substitutions
that the data source forced, and why IBIT stands in for BTC.

`SyntheticFeed` exists **only** as a deterministic test fixture. It generates
geometric Brownian motion, which has no trend to follow and no mean to revert to,
so no performance number from it means anything. The CLI prints a warning banner
whenever it is used.

## Further reading

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — module contracts, intra-bar ordering
- [docs/RISKS.md](docs/RISKS.md) — what this does not protect against
- [docs/DATA.md](docs/DATA.md) — cache schema and provenance
