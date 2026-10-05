# Architecture

## Layering

```
feeds/          bars in, nothing else           (synthetic | cached | llmquant)
   |
indicators      pure functions of values[:i+1]
   |
strategy        direction + timing only
   |
risk            size, correlation, drawdown, KILL
   |
gate            fail-closed conviction veto  <-- reads shared/signals/conviction.json
   |
execution       slippage, fees, stop resolution
   |
backtest        chronological loop, fixed intra-bar order
   |
metrics +       performance AND the benchmark it must beat
benchmark
```

Each layer has exactly one job, which is what makes each testable alone. A
strategy cannot size a position; `risk` cannot veto on conviction; `gate` cannot
compute a signal.

## Intra-bar ordering

This is the most important contract in the engine. For each bar, in order:

1. **Fill the entry queued by the previous bar**, at this bar's open.
2. **Resolve stops** against this bar's high/low.
3. **Ask the strategy**, which sees bars `0..i`.
4. **Queue** a new entry for the *next* bar; apply exits at this close.
5. **Mark equity once per timestamp**, after every symbol at that instant.

Why each ordering constraint exists:

- **1 before 3** — a signal computed from a close cannot be filled at that same
  close. Acting on a price you only know because the bar finished is the most
  common way a backtest invents returns that cannot be captured live.
- **2 before 3** — a position stopped out on this bar must not also generate a
  signal on the bar that killed it.
- **5 per timestamp, not per event** — five symbols sharing one timestamp must
  emit one mark. Marking per (symbol, bar) makes the return series non-uniform in
  time and roughly doubles the inferred annualisation factor.

## No-lookahead guarantee

Every indicator value at index `i` is a function of `values[:i+1]` only. The
`*_series` helpers compute left to right and never read ahead, so indexing a
precomputed series at `i` is mathematically identical to recomputing from scratch
with history truncated at `i`. That makes the precompute an optimisation rather
than a leak.

This is **asserted, not assumed**: `tests/test_no_lookahead.py` multiplies every
bar from index 50 onward by 5× and requires that all values and decisions over
bars 0–49 are bit-identical. It also asserts the mutation changes the *suffix*, so
the test cannot pass vacuously.

The Donchian channel takes `shift=1` for a related reason: with `shift=0` the
current bar's own high is inside the channel it must exceed, so a breakout can
never fire and the strategy silently does nothing.

## The conviction gate

A sidecar process writes `shared/signals/conviction.json`; the engine reads it.
Reads are local-disk only — no network call, no LLM call, nothing that can hang
the loop. Writes are atomic (`os.replace`), so a half-written ledger is never
observable.

Every non-approval path denies:

| Condition | Result |
|---|---|
| no ledger file / corrupt JSON | deny |
| no record for the symbol | deny (unless `allow_when_no_signal`) |
| `now > valid_until` | deny (stale) |
| `as_of > now` | deny (lookahead via the ledger) |
| conviction non-numeric, bool, or NaN | deny |
| conviction < threshold | deny |
| `shared/control/KILL` exists | deny |
| **any raised exception** | deny |

The exception clause is the point. A gate that raises and lets a caller's
`except` decide is a gate that fails open the first time something unexpected
happens.

Event-risk dampening: a record may carry `event_risk_probability`. Above the
configured threshold, conviction is capped at `1 − p`, so a market pricing a 70%
chance of an adverse event caps confidence at 30%.

## Accounting identity

```
ending_equity == starting_equity + sum(trade.pnl for trade in trades)
```

Asserted to within 1e-6 in `tests/test_backtest.py`. It holds because
`cash_delta` (gross minus the **exit** fee) and `trade.pnl` (gross minus **both**
fees) are kept distinct — the entry fee is charged to cash when the position
opens, so adding it again at close would double-count it. An earlier draft did
exactly that and the identity was off by the sum of entry fees.
