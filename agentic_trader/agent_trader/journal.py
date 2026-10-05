"""CSV persistence for trades and daily PnL."""

from __future__ import annotations

import csv
import os
import tempfile
from collections import OrderedDict
from collections.abc import Sequence
from pathlib import Path

from agent_trader.types import EquityPoint, Trade

TRADE_FIELDS = [
    "symbol", "strategy", "side", "units", "entry_ts", "entry_price",
    "exit_ts", "exit_price", "pnl", "fees", "exit_reason",
]
DAILY_FIELDS = ["date", "equity_open", "equity_close", "pnl", "return_pct", "bars"]


def _atomic_write(path: Path, rows: list[dict], fields: list[str]) -> None:
    """Write a CSV via rename-into-place so a reader never sees a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
        os.chmod(tmp, 0o644)  # mkstemp defaults to 0600; data files are not secrets
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_trades(path: str | Path, trades: Sequence[Trade]) -> Path:
    """Write one row per closed round trip."""
    target = Path(path)
    rows = [
        {
            "symbol": t.symbol,
            "strategy": t.strategy,
            "side": t.side.value,
            "units": f"{t.units:.8f}",
            "entry_ts": t.entry_ts.isoformat(),
            "entry_price": f"{t.entry_price:.6f}",
            "exit_ts": t.exit_ts.isoformat(),
            "exit_price": f"{t.exit_price:.6f}",
            "pnl": f"{t.pnl:.6f}",
            "fees": f"{t.fees:.6f}",
            "exit_reason": t.exit_reason,
        }
        for t in trades
    ]
    _atomic_write(target, rows, TRADE_FIELDS)
    return target


def write_daily_pnl(path: str | Path, curve: Sequence[EquityPoint]) -> Path:
    """Collapse the equity curve into one row per calendar day.

    Uses the first and last mark within each day, so the daily return reflects the
    day's full move rather than a single sampled point.
    """
    target = Path(path)
    by_day: OrderedDict[str, list[EquityPoint]] = OrderedDict()
    for pt in curve:
        by_day.setdefault(pt.ts.date().isoformat(), []).append(pt)

    rows = []
    for day, pts in by_day.items():
        first, last = pts[0].equity, pts[-1].equity
        rows.append({
            "date": day,
            "equity_open": f"{first:.2f}",
            "equity_close": f"{last:.2f}",
            "pnl": f"{last - first:.2f}",
            "return_pct": f"{((last - first) / first * 100) if first else 0.0:.4f}",
            "bars": len(pts),
        })
    _atomic_write(target, rows, DAILY_FIELDS)
    return target
