# TODO — agentic_trader

Plan: multi-asset backtest & research framework, stdlib-only, inside the graphify repo.
Scope boundary: **backtest/research only — no broker order wiring.**

## Phase 1 — Core domain
- [x] `types.py` — Bar, Side, Signal, Trade, Position, GateDecision
- [x] `config.py` — RiskConfig / StrategyConfig / GateConfig / AppConfig + JSON round-trip
- [x] `indicators.py` — pure-Python SMA, EMA, STD, ATR, Bollinger, Donchian, RSI, volume surge

## Phase 2 — Strategy & risk
- [x] `strategy.py` — MeanReversion (SPY 1.5σ / QQQ 1.8σ), MomentumBreakout (BTC), TrendFollowing (GLD/USO)
- [x] `risk.py` — 1% ATR sizing, SPY+QQQ→block-BTC correlation filter, 10% drawdown breaker, KILL switch
- [x] `gate.py` — fail-closed ConvictionGate + sidecar ledger reader

## Phase 3 — Engine
- [x] `execution.py` — slippage, fees, trailing stops, fill simulation
- [x] `backtest.py` — chronological loop, no lookahead
- [x] `metrics.py` — Sharpe, Sortino, win rate, profit factor, max DD
- [x] `journal.py` — trades.csv / daily_pnl.csv
- [x] `feeds/` — base protocol, synthetic (TEST FIXTURE ONLY), cached (real path), llmquant (needs key)
- [x] `cli.py` — backtest / report / cache-status

## Phase 4 — Tests
- [x] indicators, strategies, risk, gate, execution, journal/config
- [x] no-lookahead property test (5x future prices ⇒ identical past signals)
- [x] gate proof test (conviction 0.20 ⇒ 0 trades; 0.85 ⇒ trades)

## Phase 5 — Real data & verification
- [x] Fetch real OHLCV via RobinHood MCP → `data/cache/*.json`
- [x] Run real-data backtest; report result honestly even if negative
- [x] Confirm graphify not regressed (baseline: 15 pre-existing shallow-clone failures)
- [x] `git status` shows only `agentic_trader/` + `tasks/`
- [x] Fresh verification subagent re-checks diff and re-runs suite

## Review
_(filled in at completion)_

---

## Review (2026-10-05)

**Delivered.** `agentic_trader/` — 14 modules, 206 tests, 4 docs, 2 scripts.
Standard-library only; `pyproject.toml` and `uv.lock` untouched.

**The result, after a significant correction.** On 630 real daily bars the book
returns **+3.26%** against **+60.75%** for equal-weight buy-and-hold — a 57-point
shortfall with a 10% drawdown taken to get there. PF 1.09 and Sharpe +0.24 over
one untuned window are noise, not edge.

**The originally-reported −5.89% was wrong, and the cause was my own bug.** The
trailing stop was ratcheted with a bar's own high then tested against that bar's
low — intra-bar lookahead. Fixing it moved the result 9.15 points and flipped its
sign. Found by an independent verification pass, NOT by the 192-test suite, which
passed under both orderings. Now pinned by a golden test proven to fail if
reverted.

Two findings that outrank the headline: the 10% drawdown breaker makes the outcome
*worse* (+3.26% -> -5.06%, by liquidating at the first 10% drawdown), and gap
risk is the dominant loss channel (17 of 140 trades, -$611 average vs -$173 for an
ordinary stop-out).

**Ten bugs caught before merge — five by my own verification, five by an independent pass:**

1. `sharpe([0.01]*10)` returned **8.68e16** instead of 0. The `sd == 0` guard never
   fired because the float variance of a constant series is a denormal (~2e-18),
   not exactly zero. Fixed with a relative tolerance.
2. **Accounting identity was off by $291.22** — exactly the sum of entry fees. The
   entry fee was charged to cash but excluded from `trade.pnl`. Now `cash_delta`
   and `trade.pnl` are deliberately distinct and the identity holds to 0.0.
3. **Annualisation inflated Sharpe ~2.3×** for equities. Median bar spacing reads a
   year of SPY 15m bars as 35,064 periods; only ~6,552 exist, because markets close
   overnight. Switched to marks-over-elapsed-calendar-time.
4. **Equity was marked once per (symbol, bar), not per timestamp**, making the
   return series non-uniform in time and doubling the annualisation factor.
5. **"Constant dollar risk" was not true.** The notional cap binds before the ATR
   target for every realistic instrument, so realised risk is below 1% (GLD $168,
   BTC $632 against a $1,000 target). The engine now reports which constraint bound
   instead of claiming a flat 1%.

**Five more found by the independent verification pass:**

6. **HIGH — intra-bar lookahead in the trailing stop.** Ratcheted with a bar's own
   high, then tested against that bar's low. Worth 9.15 points and flipped the
   headline's sign. Invisible to the suite; now pinned by a golden test proven to
   fail on revert.
7. `size_detail` raised `ZeroDivisionError` when `atr * atr_multiple` underflowed
   to exactly 0.0 while both factors were positive (e.g. 5e-324 x 1e-9). The guard
   checked the factors, not the product.
8. NaN leaked past that same guard (`nan <= 0` is False), returning a confident
   `units=200.0` with `implied_risk=nan`. `_consider_entry` had the same hole, so a
   NaN ATR from a malformed feed could have opened a position of unknowable risk.
   Both now use `math.isfinite`.
9. The equity curve appended a duplicate final timestamp, producing a return over
   zero elapsed time and making `infer_periods_per_year` report 252 where 251 was
   right. The final mark now replaces rather than duplicates.
10. `StrategyConfig` had no validation, so `atr_stop_multiple=0` was accepted and
    silently produced a 0-trade run that read as "the strategy never triggered".

**Two of my own regression tests initially failed to catch bug 6** — they tested
the components in the right order by hand, reproducing the blind spot. One asserted
an invariant ("a long stop exit cannot fill above entry") that is false for a
trailing stop. Recorded in lessons.md.

**One thing I could not verify:** the LLMQuant feed. `LLMQUANT_API_KEY` is unset,
so it is tested only against a fake transport and is marked unverified in the code,
the docs and the README.

**Three spec deviations forced by the data source**, documented in
`config.daily_book()` and `docs/DATA.md`: no `15minute` interval exists in the API;
RTH `4hour` bars collapse to ~1/session so daily is the honest label; and there is
no crypto historicals tool, so IBIT stands in for BTC as an explicitly-labelled
proxy that gaps overnight while bitcoin does not.

**Out of scope and deliberately absent:** any order submission, broker credentials,
or call to `place_equity_order` / `place_crypto_order` / `place_option_order`.
