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
| Strategy book | **−6.37%** | 70 | 35.7% | 0.69 | −0.67 | 10.10% (halted) |
| Strategy, breaker off | **−5.89%** | 144 | 40.3% | 0.84 | −0.45 | 14.21% |
| Equal-weight buy & hold | **+60.75%** | — | — | — | — | — |

**The book lost ~6% over a window in which simply holding the same five
instruments returned ~61%.** A shortfall of roughly 67 percentage points. Every
strategy lost money and every symbol lost money.

This is not a costs problem — fees were $517, about 9% of the loss. The diagnosis
is in the exit reasons:

```
  73 trades  −$19,856  stop_hit
  41 trades  +$19,171  reverted_to_mean
  29 trades   −$5,393  stop_gap_through_open
```

Mean reversion's winners were almost exactly cancelled by its stop-outs, and gap
losses finished the job. That is what short-volatility logic does in a trending
market: it sells strength and buys weakness while the trend keeps going, and
normal pullbacks take out ATR stops. Trend following fired only 8 times in 630
bars, because EMA50/200 crossovers are rare, and lost on 6 of them.

**Treat this as a working engine with an unprofitable strategy book, not as a
trading system.** The engine is verified; the edge is absent.

## Quick start

```bash
python3 -m agent_trader cache-status                                  # what data is cached
python3 -m agent_trader backtest --feed cached --conviction 0.85      # real bars
python3 -m agent_trader backtest --feed cached --conviction 0.20      # gate veto: 0 trades
python3 -m agent_trader gate-demo                                     # fail-closed matrix
python3 -m unittest discover -s tests -t .                            # 176 tests
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
