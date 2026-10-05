"""Bars read from a local JSON cache. **This is the real-data path.**

Why a cache file rather than a direct API call: market data reaches this project
through MCP connectors (RobinHood, LLMQuant) that are available to the *agent*
driving a session, not to a Python process this library spawns. The library cannot
call them, and a docstring implying otherwise would be a lie in the code.

So the boundary is explicit and on disk:

    agent (MCP tools)  ->  data/cache/<SYMBOL>_<interval>.json  ->  CachedFeed

An operator or agent refreshes the cache; the engine only ever reads it. That also
makes a backtest reproducible: the exact bars used are committed artifacts rather
than whatever an API returned that afternoon.

Cache format -- a JSON object, not a bare array, so provenance travels with the
data and a stale or mystery file can be identified later::

    {
      "symbol": "SPY",
      "interval": "15m",
      "source": "robinhood-mcp:get_equity_historicals",
      "fetched_at": "2026-10-05T12:00:00",
      "bars": [
        {"ts": "2026-09-02T13:30:00", "open": 1.0, "high": 2.0,
         "low": 0.5, "close": 1.5, "volume": 1000.0}
      ]
    }
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_trader.feeds.base import FeedError
from agent_trader.types import Bar

REQUIRED_BAR_KEYS = ("ts", "open", "high", "low", "close", "volume")


def cache_filename(symbol: str, interval: str) -> str:
    """Filesystem-safe cache name. ``BTC-USD`` -> ``BTC-USD_1h.json``."""
    safe = symbol.replace("/", "-").replace(" ", "")
    return f"{safe}_{interval}.json"


class CachedFeed:
    """Reads bars written by the agent from an MCP data source."""

    is_synthetic = False

    def __init__(self, cache_dir: str | Path = "data/cache") -> None:
        self.cache_dir = Path(cache_dir)

    def path_for(self, symbol: str, interval: str) -> Path:
        return self.cache_dir / cache_filename(symbol, interval)

    def available(self) -> list[tuple[str, str, int, str]]:
        """``(symbol, interval, bar_count, fetched_at)`` for every cached file."""
        out = []
        if not self.cache_dir.is_dir():
            return out
        for p in sorted(self.cache_dir.glob("*.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                out.append((
                    d.get("symbol", p.stem),
                    d.get("interval", "?"),
                    len(d.get("bars", [])),
                    d.get("fetched_at", "?"),
                ))
            except (OSError, json.JSONDecodeError):
                out.append((p.stem, "?", 0, "UNREADABLE"))
        return out

    def load(self, symbol: str, interval: str) -> list[Bar]:
        path = self.path_for(symbol, interval)
        if not path.exists():
            raise FeedError(
                f"no cached bars for {symbol} {interval} at {path}. "
                f"Populate it with the agent's MCP data tools "
                f"(see docs/DATA.md), then re-run."
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FeedError(f"cache {path} unreadable: {exc}") from exc
        return bars_from_payload(payload, symbol)


def bars_from_payload(payload: dict[str, Any], symbol: str) -> list[Bar]:
    """Validate and convert a cache payload into sorted :class:`Bar` objects.

    Strict on purpose: a silently-dropped malformed bar becomes a mystery gap in a
    backtest months later, so anything unparseable raises instead.
    """
    raw = payload.get("bars")
    if not isinstance(raw, list):
        raise FeedError(f"cache for {symbol}: 'bars' must be a list")

    bars: list[Bar] = []
    for idx, row in enumerate(raw):
        if not isinstance(row, dict):
            raise FeedError(f"cache for {symbol}: bar {idx} is not an object")
        missing = [k for k in REQUIRED_BAR_KEYS if k not in row]
        if missing:
            raise FeedError(f"cache for {symbol}: bar {idx} missing {missing}")
        try:
            ts = datetime.fromisoformat(str(row["ts"]).replace("Z", "+00:00"))
        except ValueError as exc:
            raise FeedError(f"cache for {symbol}: bar {idx} bad ts {row['ts']!r}") from exc
        try:
            bar = Bar(
                symbol=payload.get("symbol", symbol),
                ts=ts.replace(tzinfo=None),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
        except (TypeError, ValueError) as exc:
            raise FeedError(f"cache for {symbol}: bar {idx} invalid: {exc}") from exc
        bars.append(bar)

    bars.sort(key=lambda b: b.ts)
    # Duplicate timestamps would double-count a bar in the merged timeline.
    seen: set[datetime] = set()
    for b in bars:
        if b.ts in seen:
            raise FeedError(f"cache for {symbol}: duplicate timestamp {b.ts}")
        seen.add(b.ts)
    return bars


def write_cache(
    cache_dir: str | Path,
    symbol: str,
    interval: str,
    bars: list[dict[str, Any]],
    source: str,
) -> Path:
    """Write a cache file atomically. Validates before committing to disk."""
    import os
    import tempfile

    d = Path(cache_dir)
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "symbol": symbol,
        "interval": interval,
        "source": source,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "bars": bars,
    }
    bars_from_payload(payload, symbol)  # fail before writing, not after
    target = d / cache_filename(symbol, interval)
    fd, tmp = tempfile.mkstemp(dir=str(d), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        os.chmod(tmp, 0o644)  # mkstemp defaults to 0600; data files are not secrets
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target
