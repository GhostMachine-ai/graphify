"""Pure-Python technical indicators.

Two deliberate design choices:

1. **No third-party dependency.** graphify's CI installs with ``uv run --frozen``
   from a committed lockfile, so adding numpy here would churn the lock and break
   that invariant. These are plenty fast for a few thousand bars.

2. **Every value at index ``i`` is a function of ``values[:i+1]`` only.** The
   ``*_series`` helpers compute left to right and never read ahead, so indexing a
   precomputed series at ``i`` is mathematically identical to recomputing from
   scratch with data truncated at ``i`` -- which is what makes the engine's
   no-lookahead guarantee structural instead of a convention. ``tests/
   test_no_lookahead.py`` asserts this rather than trusting it.

Standard deviation is **population** (ddof=0), matching the usual Bollinger
convention. ATR and RSI use **Wilder's** smoothing, not a simple average.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from agent_trader.types import Bar

Num = float | None


def _window(values: Sequence[float], period: int, end: int) -> list[float] | None:
    """The ``period`` values ending at ``end`` inclusive, or None if insufficient."""
    if period <= 0:
        raise ValueError("period must be positive")
    if end < 0 or end >= len(values):
        return None
    start = end - period + 1
    if start < 0:
        return None
    return list(values[start : end + 1])


def sma(values: Sequence[float], period: int, end: int) -> Num:
    """Simple moving average ending at ``end``."""
    win = _window(values, period, end)
    if win is None:
        return None
    return sum(win) / period


def stdev(values: Sequence[float], period: int, end: int) -> Num:
    """Population standard deviation over the window ending at ``end``."""
    win = _window(values, period, end)
    if win is None:
        return None
    mean = sum(win) / period
    var = sum((v - mean) ** 2 for v in win) / period
    return math.sqrt(var)


def sma_series(values: Sequence[float], period: int) -> list[Num]:
    """Rolling SMA aligned to ``values``; None until the window fills.

    Uses a running sum, so this is O(n) rather than O(n*period).
    """
    out: list[Num] = [None] * len(values)
    if period <= 0:
        raise ValueError("period must be positive")
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= period:
            running -= values[i - period]
        if i >= period - 1:
            out[i] = running / period
    return out


def ema_series(values: Sequence[float], period: int) -> list[Num]:
    """Rolling EMA, seeded with the first full SMA (the conventional seeding).

    Element ``i`` depends only on ``values[:i+1]``.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    out: list[Num] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1.0)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = (values[i] - prev) * k + prev
        out[i] = prev
    return out


def ema(values: Sequence[float], period: int, end: int) -> Num:
    """EMA at ``end``. Recomputes from the start; use ``ema_series`` in loops."""
    if end < 0 or end >= len(values):
        return None
    return ema_series(values[: end + 1], period)[end]


def true_range(bar: Bar, prev_close: float | None) -> float:
    """Wilder's true range. First bar falls back to the high-low span."""
    hl = bar.high - bar.low
    if prev_close is None:
        return hl
    return max(hl, abs(bar.high - prev_close), abs(bar.low - prev_close))


def atr_series(bars: Sequence[Bar], period: int = 14) -> list[Num]:
    """Wilder-smoothed ATR aligned to ``bars``."""
    if period <= 0:
        raise ValueError("period must be positive")
    out: list[Num] = [None] * len(bars)
    if len(bars) < period:
        return out
    trs = [true_range(b, bars[i - 1].close if i > 0 else None) for i, b in enumerate(bars)]
    prev = sum(trs[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(bars)):
        prev = (prev * (period - 1) + trs[i]) / period
        out[i] = prev
    return out


def atr(bars: Sequence[Bar], period: int, end: int) -> Num:
    """ATR at ``end``. Recomputes from the start; use ``atr_series`` in loops."""
    if end < 0 or end >= len(bars):
        return None
    return atr_series(bars[: end + 1], period)[end]


def bollinger(
    values: Sequence[float], period: int, sigma: float, end: int
) -> tuple[float, float, float] | None:
    """``(lower, middle, upper)`` at ``end``, or None if the window is short."""
    mid = sma(values, period, end)
    sd = stdev(values, period, end)
    if mid is None or sd is None:
        return None
    return (mid - sigma * sd, mid, mid + sigma * sd)


def donchian(
    bars: Sequence[Bar], period: int, end: int, shift: int = 1
) -> tuple[float, float] | None:
    """``(lowest_low, highest_high)`` over ``period`` bars ending ``shift`` before ``end``.

    ``shift=1`` is the default and it matters: a breakout must be measured against
    the channel formed by *prior* bars. Including the current bar makes its own high
    part of the channel it is meant to break, which never triggers and silently
    disables the strategy.
    """
    if shift < 0:
        raise ValueError("shift must be >= 0")
    anchor = end - shift
    highs = _window([b.high for b in bars], period, anchor)
    lows = _window([b.low for b in bars], period, anchor)
    if highs is None or lows is None:
        return None
    return (min(lows), max(highs))


def rsi_series(values: Sequence[float], period: int = 14) -> list[Num]:
    """Wilder-smoothed RSI aligned to ``values``."""
    if period <= 0:
        raise ValueError("period must be positive")
    out: list[Num] = [None] * len(values)
    if len(values) <= period:
        return out
    gains, losses = [0.0], [0.0]
    for i in range(1, len(values)):
        delta = values[i] - values[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains[1 : period + 1]) / period
    avg_loss = sum(losses[1 : period + 1]) / period
    out[period] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1 + avg_gain / avg_loss)
    for i in range(period + 1, len(values)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1 + avg_gain / avg_loss)
    return out


def rsi(values: Sequence[float], period: int, end: int) -> Num:
    if end < 0 or end >= len(values):
        return None
    return rsi_series(values[: end + 1], period)[end]


def volume_surge(
    volumes: Sequence[float], period: int, multiple: float, end: int
) -> bool:
    """True when ``volumes[end]`` is at least ``multiple`` times the trailing average.

    The average excludes the current bar, so a single huge bar cannot inflate the
    baseline it is being compared against.
    """
    baseline = sma(volumes, period, end - 1)
    if baseline is None or baseline <= 0:
        return False
    return volumes[end] >= multiple * baseline
