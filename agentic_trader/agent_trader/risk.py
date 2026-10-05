"""Position sizing and portfolio risk limits."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_trader.config import RiskConfig
from agent_trader.types import GateDecision, Position, Side

#: Longs in both of these block a new BTC long. Risk-on equity exposure and a
#: crypto long are the same macro bet wearing different clothes; stacking them
#: concentrates risk the per-trade sizing cannot see.
CORRELATION_BLOCK_REQUIRES = ("SPY", "QQQ")
CORRELATION_BLOCK_TARGET = "BTC-USD"


@dataclass(frozen=True)
class SizingDecision:
    """Result of sizing one position, including *why* it came out that size."""

    units: float
    #: Either ``atr_risk_target`` or ``notional_cap`` -- which limit actually bound.
    binding_constraint: str
    #: Dollars lost if price moves the full stop distance. <= equity * risk_per_trade.
    implied_risk_dollars: float
    notional: float

    @property
    def risk_fraction_of(self) -> float:
        return self.implied_risk_dollars


@dataclass
class RiskState:
    """Mutable portfolio risk state across a run."""

    equity: float
    peak_equity: float
    halted: bool = False
    halt_reason: str = ""

    def mark(self, equity: float) -> None:
        self.equity = equity
        if equity > self.peak_equity:
            self.peak_equity = equity

    @property
    def drawdown(self) -> float:
        """Fractional drawdown from peak. Zero when at or above the prior peak."""
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, (self.peak_equity - self.equity) / self.peak_equity)


class RiskManager:
    """Sizes positions and enforces portfolio-level limits."""

    def __init__(self, config: RiskConfig, root: str | Path = ".") -> None:
        self.config = config
        self.root = Path(root)
        self.state = RiskState(
            equity=config.starting_equity, peak_equity=config.starting_equity
        )

    # ---------------------------------------------------------------- sizing

    def size_detail(
        self, equity: float, atr: float, atr_multiple: float, price: float
    ) -> SizingDecision:
        """Size a position under two independent constraints, returning both.

        **These two constraints conflict, and the tighter one wins.** That is not a
        rough edge to hide; it is the central fact about sizing an unleveraged
        account off ATR:

        1. *ATR risk target* -- ``units = (equity * risk_per_trade) / (atr * mult)``,
           so an ``mult``-ATR adverse move costs exactly ``risk_per_trade``.
        2. *Notional cap* -- ``units = (equity * max_notional_per_position) / price``,
           which keeps one position from dominating the book or implying leverage.

        For a low-volatility instrument the ATR target demands large exposure: GLD
        with ATR 0.80 at $190 needs ~$237,500 of notional to put $1,000 at risk on a
        1-ATR stop -- 2.4x leverage on a $100k account. The cap refuses that, so
        **realized risk per trade is <= ``risk_per_trade``, often well below it.**
        A backtest that reported a flat 1% risk per trade while silently capping
        notional would be misreporting its own risk, so the binding constraint is
        returned and the engine records it.
        """
        if atr <= 0 or atr_multiple <= 0 or price <= 0 or equity <= 0:
            return SizingDecision(0.0, "invalid_inputs", 0.0, 0.0)

        risk_dollars = equity * self.config.risk_per_trade
        stop_distance = atr * atr_multiple
        atr_units = risk_dollars / stop_distance
        cap_units = (equity * self.config.max_notional_per_position) / price

        if atr_units <= cap_units:
            units, bound = atr_units, "atr_risk_target"
        else:
            units, bound = cap_units, "notional_cap"

        return SizingDecision(
            units=units,
            binding_constraint=bound,
            implied_risk_dollars=units * stop_distance,
            notional=units * price,
        )

    def position_size(
        self, equity: float, atr: float, atr_multiple: float, price: float
    ) -> float:
        """Units to trade. See :meth:`size_detail` for which constraint binds."""
        return self.size_detail(equity, atr, atr_multiple, price).units

    # ------------------------------------------------------------ portfolio

    def check_drawdown(self) -> GateDecision:
        """Halt permanently once the drawdown limit is breached."""
        dd = self.state.drawdown
        if dd >= self.config.max_portfolio_drawdown:
            self.state.halted = True
            self.state.halt_reason = (
                f"max_drawdown_breached_{dd:.2%}>={self.config.max_portfolio_drawdown:.2%}"
            )
            return GateDecision(False, self.state.halt_reason)
        return GateDecision(True, "drawdown_ok")

    def correlation_block(
        self, symbol: str, side: Side, open_positions: dict[str, Position]
    ) -> GateDecision:
        """Deny a BTC long while both SPY and QQQ are held long."""
        if not self.config.correlation_filter_enabled:
            return GateDecision(True, "correlation_filter_disabled")
        if symbol != CORRELATION_BLOCK_TARGET or side is not Side.LONG:
            return GateDecision(True, "not_correlation_constrained")
        longs = {
            s
            for s, p in open_positions.items()
            if p.side is Side.LONG and s in CORRELATION_BLOCK_REQUIRES
        }
        if len(longs) == len(CORRELATION_BLOCK_REQUIRES):
            return GateDecision(
                False, "correlation_block_spy_and_qqq_already_long"
            )
        return GateDecision(True, "correlation_ok")

    def kill_switch_engaged(self) -> bool:
        """True when the operator's KILL file is present.

        A file is deliberately the mechanism: it can be created by a human with
        ``touch`` from any shell, with no dependency on this process being healthy.
        """
        return (self.root / "shared" / "control" / "KILL").exists()

    def can_open(
        self,
        symbol: str,
        side: Side,
        open_positions: dict[str, Position],
    ) -> GateDecision:
        """Every portfolio-level check that must pass before a new entry."""
        if self.state.halted:
            return GateDecision(False, self.state.halt_reason or "halted")
        if self.kill_switch_engaged():
            return GateDecision(False, "kill_switch_engaged")
        dd = self.check_drawdown()
        if not dd.allowed:
            return dd
        if symbol in open_positions:
            return GateDecision(False, "already_in_position")
        if len(open_positions) >= self.config.max_concurrent_positions:
            return GateDecision(False, "max_concurrent_positions")
        return self.correlation_block(symbol, side, open_positions)

    # --------------------------------------------------------------- stops

    def initial_stop(self, side: Side, price: float, atr: float, mult: float) -> float:
        """Hard stop placed ``mult`` ATRs adverse to entry."""
        return price - side.sign * atr * mult

    def trail_stop(self, position: Position, mult: float) -> float:
        """Stop ratcheted from the best price seen since entry.

        Monotonic by construction: ``max``/``min`` against the existing stop means
        a stop never loosens, which is the property that makes a trailing stop a
        risk control rather than a suggestion.
        """
        candidate = position.extreme_price - position.side.sign * position.atr_at_entry * mult
        if position.side is Side.LONG:
            return max(position.stop_price, candidate)
        return min(position.stop_price, candidate)
