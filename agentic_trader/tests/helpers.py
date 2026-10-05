"""Shared test helpers."""

from __future__ import annotations

from datetime import datetime, timedelta

from agent_trader.types import Bar

T0 = datetime(2026, 1, 5, 9, 30)


def bars_from_closes(
    closes: list[float],
    symbol: str = "TEST",
    volume: float = 1000.0,
    step: timedelta = timedelta(hours=1),
    wick: float = 0.5,
) -> list[Bar]:
    """Build bars from a close series, with symmetric wicks around each close."""
    out = []
    for i, c in enumerate(closes):
        out.append(Bar(symbol, T0 + step * i, c, c + wick, c - wick, c, volume))
    return out


def ledger_for(
    symbols: list[str],
    conviction: float,
    now: datetime = T0,
    valid_days: int = 4000,
    **extra,
) -> dict:
    """A conviction ledger that is fresh and valid for every symbol."""
    rec = {
        "as_of": (now - timedelta(days=1)).isoformat(),
        "valid_until": (now + timedelta(days=valid_days)).isoformat(),
        "conviction": conviction,
    }
    rec.update(extra)
    return {"signals": {s: dict(rec) for s in symbols}}
