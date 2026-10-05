#!/usr/bin/env python3
"""Convert a saved RobinHood MCP ``get_equity_historicals`` result into bar caches.

Why this exists: the MCP data tools belong to the agent, not to this library (see
``agent_trader/feeds/cached.py``). A large historicals response is written to a
tool-result file on disk, and this script turns that file into the cache format
the engine reads -- so market data never has to pass through an agent's context
to get here, and the exact bars used by a backtest become a committed artifact.

Usage::

    python3 scripts/ingest_robinhood.py <tool-result.json> [--interval 1d]

The RobinHood bar shape is ``{begins_at, open_price, high_price, low_price,
close_price, volume, session}`` with prices as strings. ``begins_at`` is the
bar's **left edge** in UTC; the engine treats a bar timestamp as its close, so
the interval length is added to each timestamp on the way in. Bars flagged
``interpolated`` are gap-fill and are dropped -- they carry no new information and
would otherwise show up as real price action.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_trader.feeds.cached import write_cache  # noqa: E402

#: Maps a RobinHood interval name onto (our label, bar duration).
INTERVALS = {
    "minute": ("1m", timedelta(minutes=1)),
    "5minute": ("5m", timedelta(minutes=5)),
    "10minute": ("10m", timedelta(minutes=10)),
    "30minute": ("30m", timedelta(minutes=30)),
    "hour": ("1h", timedelta(hours=1)),
    "4hour": ("4h", timedelta(hours=4)),
    "day": ("1d", timedelta(days=1)),
    "week": ("1w", timedelta(weeks=1)),
}


def convert(result: dict, label_override: str | None = None) -> tuple[str, str, list[dict], int]:
    """Return ``(symbol, interval_label, bars, dropped)`` for one result block."""
    symbol = result["symbol"]
    rh_interval = result.get("interval", "day")
    if rh_interval not in INTERVALS:
        raise SystemExit(f"unsupported interval {rh_interval!r}")
    label, duration = INTERVALS[rh_interval]
    label = label_override or label

    bars, dropped = [], 0
    for b in result.get("bars", []):
        if b.get("interpolated"):
            dropped += 1
            continue
        try:
            ts = b["begins_at"]
            from datetime import datetime

            left = datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
            bars.append({
                # Left-edge label -> close time, matching the engine's convention.
                "ts": (left + duration).isoformat(),
                "open": float(b["open_price"]),
                "high": float(b["high_price"]),
                "low": float(b["low_price"]),
                "close": float(b["close_price"]),
                "volume": float(b.get("volume") or 0.0),
            })
        except (KeyError, TypeError, ValueError) as exc:
            raise SystemExit(f"{symbol}: malformed bar {b!r}: {exc}") from exc
    return symbol, label, bars, dropped


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tool_result", help="path to the saved MCP result JSON")
    ap.add_argument("--interval", default=None,
                    help="override the interval label written to the cache")
    ap.add_argument("--cache-dir", default=None)
    args = ap.parse_args(argv)

    root = Path(__file__).resolve().parent.parent
    cache_dir = Path(args.cache_dir) if args.cache_dir else root / "data" / "cache"

    payload = json.loads(Path(args.tool_result).read_text(encoding="utf-8"))
    results = payload.get("data", {}).get("results", [])
    if not results:
        raise SystemExit("no results in that file")

    for result in results:
        symbol, label, bars, dropped = convert(result, args.interval)
        if not bars:
            print(f"  {symbol}: no usable bars, skipped", file=sys.stderr)
            continue
        path = write_cache(
            cache_dir, symbol, label, bars,
            source="robinhood-mcp:get_equity_historicals",
        )
        note = f" ({dropped} interpolated dropped)" if dropped else ""
        print(f"  {symbol:8s} {label:4s} {len(bars):5d} bars  "
              f"{bars[0]['ts'][:10]}..{bars[-1]['ts'][:10]}{note}  -> {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
