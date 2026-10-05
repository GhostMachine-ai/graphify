"""Fill simulation: slippage, fees, and stop resolution.

Every assumption here either flatters or penalises a backtest, so each one is
stated explicitly and the pessimistic choice is the default:

* **Fills happen at the next bar's open**, never at the signal bar's close. Acting
  on a close you only know *because the bar closed* is the most common way a
  backtest invents returns that cannot be captured live.
* **Slippage always hurts.** Buys fill above the reference, sells below it.
* **A gap through the stop fills at the open, not at the stop.** Assuming the stop
  price is honoured through a gap is the second most common way backtests overstate
  results; real stops become market orders at the gap.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_trader.config import ExecutionConfig
from agent_trader.types import Bar, Position, Side, Trade

BPS = 1e-4


@dataclass(frozen=True)
class Fill:
    """A simulated execution."""

    price: float
    units: float
    fee: float
    slippage_cost: float
    note: str = ""


class ExecutionEngine:
    """Turns intents into fills under a stated cost model."""

    def __init__(self, config: ExecutionConfig) -> None:
        self.config = config

    # ----------------------------------------------------------- primitives

    def slipped_price(self, reference: float, side: Side, opening: bool) -> float:
        """Apply slippage in whichever direction is adverse to the trader.

        Opening a long and closing a short are both buys; opening a short and
        closing a long are both sells.
        """
        is_buy = (side is Side.LONG) if opening else (side is Side.SHORT)
        adj = reference * self.config.slippage_bps * BPS
        return reference + adj if is_buy else reference - adj

    def fee_for(self, price: float, units: float) -> float:
        return abs(price * units) * self.config.fee_bps * BPS

    def fill(
        self, reference: float, units: float, side: Side, *, opening: bool, note: str = ""
    ) -> Fill:
        price = self.slipped_price(reference, side, opening)
        return Fill(
            price=price,
            units=units,
            fee=self.fee_for(price, units),
            slippage_cost=abs(price - reference) * abs(units),
            note=note,
        )

    # ----------------------------------------------------------------- stops

    def stop_hit(self, position: Position, bar: Bar) -> tuple[bool, float, str]:
        """Whether ``bar`` triggers the stop, and at what price it would fill.

        Returns ``(hit, fill_reference, reason)``. A gap beyond the stop at the open
        fills at the open -- worse than the stop -- which is what actually happens.
        """
        stop = position.stop_price
        if position.side is Side.LONG:
            if bar.open <= stop:
                return True, bar.open, "stop_gap_through_open"
            if bar.low <= stop:
                return True, stop, "stop_hit"
        else:
            if bar.open >= stop:
                return True, bar.open, "stop_gap_through_open"
            if bar.high >= stop:
                return True, stop, "stop_hit"
        return False, 0.0, ""

    def update_extreme(self, position: Position, bar: Bar) -> None:
        """Ratchet the best price seen since entry, which drives the trail."""
        if position.side is Side.LONG:
            position.extreme_price = max(position.extreme_price, bar.high)
        else:
            position.extreme_price = min(position.extreme_price, bar.low)

    # ----------------------------------------------------------------- close

    def close_position(
        self,
        position: Position,
        reference_price: float,
        exit_ts,
        reason: str,
        entry_fee: float = 0.0,
    ) -> tuple[Trade, float]:
        """Close ``position`` and return ``(trade, cash_delta)``.

        The two numbers are deliberately different, and conflating them is an easy
        way to break the books:

        * ``cash_delta`` = gross PnL minus the **exit** fee. The entry fee was
          already deducted from cash when the position opened, so charging it again
          here would double-count it.
        * ``trade.pnl`` = gross PnL minus **both** fees -- the round trip's true
          net contribution.

        Together these make the accounting identity hold exactly::

            ending_equity == starting_equity + sum(trade.pnl for trade in trades)

        which ``tests/test_backtest.py`` asserts to the cent.
        """
        f = self.fill(reference_price, position.units, position.side, opening=False,
                      note=reason)
        gross = (f.price - position.entry_price) * position.units * position.side.sign
        cash_delta = gross - f.fee
        net = cash_delta - entry_fee
        trade = Trade(
            symbol=position.symbol,
            side=position.side,
            units=position.units,
            entry_price=position.entry_price,
            exit_price=f.price,
            entry_ts=position.entry_ts,
            exit_ts=exit_ts,
            pnl=net,
            fees=f.fee + entry_fee,
            exit_reason=reason,
            strategy=position.strategy,
        )
        return trade, cash_delta
