"""Domain types for the backtest engine.

All types are frozen dataclasses: a ``Bar`` that has been handed to a strategy must
not be mutable, or a strategy could rewrite history and defeat the no-lookahead
guarantee the engine is built around.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime


class Side(enum.Enum):
    """Direction of a position or signal."""

    LONG = "long"
    SHORT = "short"
    FLAT = "flat"

    @property
    def sign(self) -> int:
        """+1 for long, -1 for short, 0 for flat."""
        if self is Side.LONG:
            return 1
        if self is Side.SHORT:
            return -1
        return 0


class Action(enum.Enum):
    """What a strategy wants the engine to do."""

    ENTER = "enter"
    EXIT = "exit"
    HOLD = "hold"


@dataclass(frozen=True)
class Bar:
    """One OHLCV candle.

    ``ts`` is the bar's *close* time. The engine treats a bar as knowable only once
    it has closed, which is what makes intrabar lookahead impossible.
    """

    symbol: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        if self.high < self.low:
            raise ValueError(f"{self.symbol} @ {self.ts}: high {self.high} < low {self.low}")
        if not (self.low <= self.open <= self.high):
            raise ValueError(f"{self.symbol} @ {self.ts}: open {self.open} outside [low, high]")
        if not (self.low <= self.close <= self.high):
            raise ValueError(f"{self.symbol} @ {self.ts}: close {self.close} outside [low, high]")
        if self.volume < 0:
            raise ValueError(f"{self.symbol} @ {self.ts}: negative volume {self.volume}")


@dataclass(frozen=True)
class Signal:
    """A strategy's intent for one symbol at one bar."""

    symbol: str
    ts: datetime
    action: Action
    side: Side
    price: float
    reason: str
    atr: float | None = None


@dataclass
class Position:
    """An open position, mutated as the trailing stop ratchets."""

    symbol: str
    side: Side
    units: float
    entry_price: float
    entry_ts: datetime
    atr_at_entry: float
    stop_price: float
    #: Best price seen since entry; drives the trailing stop.
    extreme_price: float
    strategy: str = ""

    def unrealized(self, price: float) -> float:
        return (price - self.entry_price) * self.units * self.side.sign

    def notional(self, price: float) -> float:
        return abs(self.units) * price


@dataclass(frozen=True)
class Trade:
    """A closed round trip."""

    symbol: str
    side: Side
    units: float
    entry_price: float
    exit_price: float
    entry_ts: datetime
    exit_ts: datetime
    pnl: float
    fees: float
    exit_reason: str
    strategy: str = ""

    @property
    def is_win(self) -> bool:
        return self.pnl > 0


@dataclass(frozen=True)
class GateDecision:
    """Outcome of a conviction-gate check.

    Carries the reason so a denial is auditable rather than a bare ``False``.
    """

    allowed: bool
    reason: str
    conviction: float | None = None


@dataclass
class EquityPoint:
    """Mark-to-market equity at a point in time."""

    ts: datetime
    equity: float
    cash: float = 0.0
    open_positions: int = 0


@dataclass
class BacktestResult:
    """Everything a run produces."""

    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[EquityPoint] = field(default_factory=list)
    starting_equity: float = 0.0
    ending_equity: float = 0.0
    #: Entries refused, keyed by reason -> count. Makes the gate's effect measurable.
    denials: dict[str, int] = field(default_factory=dict)
    halted: bool = False
    halt_reason: str = ""

    def record_denial(self, reason: str) -> None:
        self.denials[reason] = self.denials.get(reason, 0) + 1
