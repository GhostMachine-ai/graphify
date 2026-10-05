"""The feed contract."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_trader.types import Bar


class FeedError(RuntimeError):
    """Raised when a feed cannot supply the requested history."""


@runtime_checkable
class BarFeed(Protocol):
    """Supplies closed OHLCV bars, oldest first.

    Implementations must return bars **sorted ascending by timestamp** and must not
    include a partially-formed current bar. A live, still-forming bar is the single
    easiest way to leak lookahead into an otherwise correct engine.
    """

    def load(self, symbol: str, interval: str) -> list[Bar]:
        """Return the available history for ``symbol`` at ``interval``."""
        ...
