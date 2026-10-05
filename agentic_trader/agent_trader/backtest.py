"""Event-driven backtest engine.

Ordering within one bar is fixed, and it matters more than almost anything else in
a backtest, so it is spelled out here and implemented in exactly this order:

1. **Fill any entry queued by the previous bar**, at this bar's open.
2. **Resolve stops** against this bar's high/low, before any new decision.
3. **Ask the strategy** about this bar (it sees bars up to and including this one).
4. **Queue** any new entry for the *next* bar; apply exits immediately at the close.
5. **Mark equity once per timestamp**, after every symbol at that instant is done.

Step 1 preceding step 3 is what prevents acting on a close in the same instant it
becomes known. Step 2 preceding step 3 means a stopped-out position cannot also
generate a signal on the bar that killed it. Step 5 being per *timestamp* rather
than per (symbol, bar) keeps the equity curve a uniform time series, which is a
precondition for annualising anything computed from it.

The engine owns no network access, no broker client and no LLM call. Conviction
arrives only as a local file read through :mod:`agent_trader.gate`.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from agent_trader.config import AppConfig
from agent_trader.execution import ExecutionEngine
from agent_trader.gate import ConvictionGate
from agent_trader.metrics import Performance, summarize
from agent_trader.risk import RiskManager
from agent_trader.strategy import Strategy, build_strategy
from agent_trader.types import (
    Action,
    BacktestResult,
    Bar,
    EquityPoint,
    Position,
    Side,
)

SECONDS_PER_YEAR = 365.25 * 24 * 3600


@dataclass
class _PendingEntry:
    """An entry approved on bar ``i``, to be filled at bar ``i+1``'s open."""

    symbol: str
    side: Side
    atr: float
    atr_multiple: float
    strategy: str
    reason: str


def infer_periods_per_year(timestamps: Sequence[datetime]) -> int:
    """Annualisation factor: observed marks divided by elapsed calendar time.

    Inferred rather than hardcoded, because annualising a 15-minute curve as if it
    were daily inflates Sharpe by roughly an order of magnitude.

    Counting *observed marks over elapsed span* rather than taking the median gap
    is deliberate. A year of SPY 15-minute bars has a median gap of 15 minutes,
    which scales to 35,064 periods/year -- but only ~6,552 such bars actually
    exist, since the market is shut overnight and at weekends. Using the median
    would inflate Sharpe by sqrt(35064/6552) ~= 2.3x on every equity instrument.
    Dividing real marks by real elapsed time absorbs those closures automatically
    and still yields ~8,760 for genuinely continuous crypto hours.

    Falls back to 252 when the span cannot be determined.
    """
    if len(timestamps) < 3:
        return 252
    span = (timestamps[-1] - timestamps[0]).total_seconds()
    if span <= 0:
        return 252
    years = span / SECONDS_PER_YEAR
    if years <= 0:
        return 252
    return max(1, int(round((len(timestamps) - 1) / years)))


class Backtester:
    """Runs one multi-symbol backtest."""

    def __init__(self, config: AppConfig, root: str | Path = ".") -> None:
        self.config = config
        self.root = Path(root)
        self.risk = RiskManager(config.risk, root=root)
        self.gate = ConvictionGate(config.gate, root=root)
        self.execution = ExecutionEngine(config.execution)
        # Which sizing constraint bound, counted across the run, so a report can
        # say whether ATR risk or the notional cap actually governed position size.
        self.sizing_bounds: dict[str, int] = {}

    # ------------------------------------------------------------------ setup

    def _build(self, bars_by_symbol: dict[str, list[Bar]]) -> dict[str, Strategy]:
        strategies: dict[str, Strategy] = {}
        for sc in self.config.strategies:
            bars = bars_by_symbol.get(sc.symbol)
            if not bars:
                continue
            strat = build_strategy(sc)
            strat.prepare(bars)
            strategies[sc.symbol] = strat
        return strategies

    # -------------------------------------------------------------------- run

    def run(
        self,
        bars_by_symbol: dict[str, list[Bar]],
        ledger: dict | None = None,
    ) -> tuple[BacktestResult, Performance]:
        """Execute the book over the supplied history."""
        strategies = self._build(bars_by_symbol)
        result = BacktestResult(starting_equity=self.config.risk.starting_equity)

        cash = self.config.risk.starting_equity
        positions: dict[str, Position] = {}
        pending: dict[str, _PendingEntry] = {}
        entry_fees: dict[str, float] = {}
        last_close: dict[str, float] = {}

        # Symbol breaks ties so a timestamp is processed in a deterministic order.
        flat = sorted(
            (bar.ts, sym, i)
            for sym, bars in bars_by_symbol.items()
            if sym in strategies
            for i, bar in enumerate(bars)
        )
        by_ts: OrderedDict[datetime, list[tuple[str, int]]] = OrderedDict()
        for ts, sym, i in flat:
            by_ts.setdefault(ts, []).append((sym, i))

        for ts, events in by_ts.items():
            for symbol, i in events:
                bar = bars_by_symbol[symbol][i]
                strat = strategies[symbol]
                sc = strat.config
                last_close[symbol] = bar.close

                # 1. fill an entry queued on the previous bar, at this open.
                if symbol in pending:
                    cash = self._try_fill(
                        pending.pop(symbol), bar, ts, positions, entry_fees,
                        cash, result,
                    )

                # 2. resolve stops before any new decision.
                #
                # ORDER IS CRITICAL. The stop tested against this bar must be the
                # stop that was already in effect when the bar began -- NOT one
                # ratcheted up using this bar's own high.
                #
                # Raising the stop from bar.high and then testing it against
                # bar.low silently assumes the high occurred before the low, and
                # OHLC does not record the intra-bar path. For O=109 H=110 L=99
                # on a long stopped at 98 with a 2-ATR trail, ratcheting first
                # lifts the stop to 108 and then "stops out" at 108 -- booking a
                # +800 profit on a trade the stop never actually touched. A
                # trailing stop cannot profit above its own trail level; that is
                # the tell. If the path was 109 -> 99 -> 110, the stop was still
                # 98 when price reached 99 and there was no exit at all.
                #
                # So: test first, then ratchet for the NEXT bar.
                pos = positions.get(symbol)
                if pos is not None:
                    hit, ref, why = self.execution.stop_hit(pos, bar)
                    if hit:
                        trade, delta = self.execution.close_position(
                            pos, ref, ts, why, entry_fees.pop(symbol, 0.0)
                        )
                        result.trades.append(trade)
                        cash += delta
                        del positions[symbol]
                    else:
                        self.execution.update_extreme(pos, bar)
                        pos.stop_price = self.risk.trail_stop(
                            pos, sc.atr_stop_multiple
                        )

                # 3. ask the strategy.
                signal = strat.evaluate(i, positions.get(symbol))

                # 4. act.
                if signal.action is Action.EXIT and symbol in positions:
                    trade, delta = self.execution.close_position(
                        positions[symbol], bar.close, ts, signal.reason,
                        entry_fees.pop(symbol, 0.0),
                    )
                    result.trades.append(trade)
                    cash += delta
                    del positions[symbol]
                elif signal.action is Action.ENTER and symbol not in positions:
                    self._consider_entry(
                        signal, sc, strat, ts, positions, pending, result, ledger
                    )

            # 5. mark to market, once for this instant.
            equity = cash + sum(
                p.unrealized(last_close.get(s, p.entry_price))
                for s, p in positions.items()
            )
            self.risk.state.mark(equity)
            result.equity_curve.append(
                EquityPoint(ts=ts, equity=equity, cash=cash,
                            open_positions=len(positions))
            )

            dd = self.risk.check_drawdown()
            if not dd.allowed and not result.halted:
                result.halted = True
                result.halt_reason = dd.reason
                cash = self._flatten(
                    positions, entry_fees, last_close, ts,
                    "drawdown_halt_liquidation", cash, result,
                )
                pending.clear()

        final_ts = flat[-1][0] if flat else datetime.now()
        cash = self._flatten(
            positions, entry_fees, last_close, final_ts, "end_of_data", cash, result
        )

        result.ending_equity = cash
        if result.equity_curve:
            # The post-flatten mark REPLACES the in-loop mark at the same instant
            # rather than being appended beside it. Appending produced two points
            # at one timestamp, which is a return computed over zero elapsed time:
            # it broke the "uniform time series" precondition the metrics rely on,
            # made infer_periods_per_year read 252 where 251 was right, and made
            # write_daily_pnl double-count the final session.
            final = EquityPoint(ts=final_ts, equity=cash, cash=cash,
                                open_positions=0)
            if result.equity_curve[-1].ts == final_ts:
                result.equity_curve[-1] = final
            else:
                result.equity_curve.append(final)

        ppy = infer_periods_per_year([pt.ts for pt in result.equity_curve])
        perf = summarize(result.trades, result.equity_curve,
                         result.starting_equity, ppy)
        return result, perf

    # ----------------------------------------------------------------- helpers

    def _try_fill(
        self,
        p: _PendingEntry,
        bar: Bar,
        ts: datetime,
        positions: dict[str, Position],
        entry_fees: dict[str, float],
        cash: float,
        result: BacktestResult,
    ) -> float:
        """Attempt the queued entry at ``bar.open``. Returns the new cash balance.

        Risk is re-checked here, not just when the entry was queued: a bar may have
        passed, and the drawdown or correlation picture can have changed since.
        """
        decision = self.risk.can_open(p.symbol, p.side, positions)
        if not decision.allowed:
            result.record_denial(decision.reason)
            return cash

        sizing = self.risk.size_detail(
            self.risk.state.equity, p.atr, p.atr_multiple, bar.open
        )
        self.sizing_bounds[sizing.binding_constraint] = (
            self.sizing_bounds.get(sizing.binding_constraint, 0) + 1
        )
        if sizing.units <= 0:
            result.record_denial("zero_size")
            return cash

        f = self.execution.fill(
            bar.open, sizing.units, p.side, opening=True, note=p.reason
        )
        positions[p.symbol] = Position(
            symbol=p.symbol,
            side=p.side,
            units=sizing.units,
            entry_price=f.price,
            entry_ts=ts,
            atr_at_entry=p.atr,
            stop_price=self.risk.initial_stop(p.side, f.price, p.atr, p.atr_multiple),
            extreme_price=f.price,
            strategy=p.strategy,
        )
        entry_fees[p.symbol] = f.fee
        return cash - f.fee

    def _consider_entry(
        self,
        signal,
        sc,
        strat: Strategy,
        ts: datetime,
        positions: dict[str, Position],
        pending: dict[str, _PendingEntry],
        result: BacktestResult,
        ledger: dict | None,
    ) -> None:
        """Run risk then the conviction gate; queue for the next bar if both pass."""
        atr = signal.atr
        # `nan <= 0` is False, so a bare comparison would let a NaN ATR from a
        # malformed feed reach sizing and open a position of unknowable risk.
        if atr is None or not math.isfinite(atr) or atr <= 0:
            result.record_denial("no_atr")
            return
        pre = self.risk.can_open(signal.symbol, signal.side, positions)
        if not pre.allowed:
            result.record_denial(pre.reason)
            return
        g = self.gate.confirm_entry(signal.symbol, ts, ledger=ledger)
        if not g.allowed:
            result.record_denial(f"gate:{g.reason}")
            return
        pending[signal.symbol] = _PendingEntry(
            signal.symbol, signal.side, atr, sc.atr_stop_multiple,
            strat.name, signal.reason,
        )

    def _flatten(
        self,
        positions: dict[str, Position],
        entry_fees: dict[str, float],
        last_close: dict[str, float],
        ts: datetime,
        reason: str,
        cash: float,
        result: BacktestResult,
    ) -> float:
        """Close every open position at its last known close."""
        for s, p in list(positions.items()):
            trade, delta = self.execution.close_position(
                p, last_close.get(s, p.entry_price), ts, reason,
                entry_fees.pop(s, 0.0),
            )
            result.trades.append(trade)
            cash += delta
            del positions[s]
        return cash
