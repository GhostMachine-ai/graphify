# Data

## Why a cache file and not an API client

Market data reaches this project through MCP connectors (RobinHood, LLMQuant)
that are available to the **agent** driving a session, not to a Python process
this library spawns. The library genuinely cannot call them, and a docstring
implying otherwise would be false. So the boundary is explicit and on disk:

```
agent (MCP tools) → data/cache/<SYMBOL>_<interval>.json → CachedFeed → engine
```

A useful side effect: the exact bars behind a backtest become a committed
artifact, so a result is reproducible rather than depending on what an API
returned that afternoon.

## Cache schema

A JSON object, not a bare array, so provenance travels with the data:

```json
{
  "symbol": "SPY",
  "interval": "1d",
  "source": "robinhood-mcp:get_equity_historicals",
  "fetched_at": "2026-10-05T10:20:23",
  "bars": [
    {"ts": "2024-04-02T00:00:00", "open": 1.0, "high": 2.0,
     "low": 0.5, "close": 1.5, "volume": 1000.0}
  ]
}
```

`ts` is the bar's **close** time. Validation is strict: a malformed bar, a bad
timestamp or a duplicate timestamp raises rather than being silently dropped,
because a quietly-dropped bar becomes a mystery gap months later.

## Refreshing the cache

```bash
# 1. In an agent session, call the MCP historicals tool. A large response is
#    written to a tool-result file rather than returned inline.
# 2. Convert it:
python3 scripts/ingest_robinhood.py <tool-result.json>
# 3. Confirm:
python3 -m agent_trader cache-status
```

The ingest script adds the interval duration to RobinHood's left-edge
`begins_at` label to get a close time, and drops any bar flagged
`interpolated` — those are gap-fill and carry no new information.

## What is cached now

630 daily bars per symbol, 2024-04-02 to 2026-10-03, split-adjusted, regular
trading hours: **SPY, QQQ, GLD, USO, IBIT**.

## Interval substitutions the data source forced

These are deviations from the original specification. They are documented here
and in `config.daily_book()` because anyone reading a result needs them.

| Spec | Actual | Why |
|---|---|---|
| SPY/QQQ at 15m | 1d | The API offers no `15minute` interval at all. Available intraday: 15second, 30second, minute, 5minute, 10minute, 30minute, hour, 4hour. A true 15m series would have to be aggregated from 5-minute bars. |
| GLD/USO at 4h | 1d | Under regular trading hours a 4-hour bar for these ETFs *is* ~one bar per session — a 3-month request returned 66 bars. Daily is the honest label for the same series, and EMA50/200 on daily is the canonical golden cross. |
| BTC-USD at 1h | IBIT at 1d | The RobinHood MCP surface exposes crypto only as real-time quotes (`get_crypto_quotes`); there is no crypto historicals tool, so BTC spot history is unavailable. |

### IBIT is a proxy, not bitcoin

IBIT is a spot bitcoin ETF, so it is correlated exposure — but it trades only in
US market hours and therefore **gaps across every night and weekend while bitcoin
keeps moving**. It also carries fund fees and tracking error. Any conclusion about
the momentum strategy on IBIT is a conclusion about IBIT.

Note also that period lengths were kept from the spec, which changes their
meaning: ATR14 on daily bars measures two weeks of volatility where on 15-minute
bars it measured three and a half hours.

## The LLMQuant feed is unverified

`feeds/llmquant.py` targets the uploaded `@llmquant/data-mcp` service. It is
**not verified against the live API** — `LLMQUANT_API_KEY` was not set in the
environment it was written in, so it is exercised only against an injected fake
transport. The request shape follows the package's documented configuration, but
response parsing is a best-effort mapping over several plausible field namings and
should be confirmed before anyone trusts a number that came through it.

## Synthetic data is a test fixture only

`SyntheticFeed` generates geometric Brownian motion: increments are independent,
so there is no trend to follow, no mean to revert to, and no volume-price
relationship to confirm. **No performance number from it means anything.** It
exists because determinism makes engine mechanics testable exactly. The CLI prints
a warning banner whenever it is used.
