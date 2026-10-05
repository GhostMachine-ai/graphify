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

**Delivered.** `agentic_trader/` — 14 modules, 192 tests, 4 docs, 2 scripts.
Standard-library only; `pyproject.toml` and `uv.lock` untouched.

**The result is negative and that is the headline.** On 630 real daily bars the
book returned −5.89% while equal-weight buy-and-hold on the same five instruments
returned +60.75%. Every strategy lost; every symbol lost. Fees were only 9% of the
loss, so this is a strategy problem, not a cost problem. The engine is verified;
the edge is absent. The README leads with this rather than burying it.

**Five bugs my own verification caught, all in my first drafts:**

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
