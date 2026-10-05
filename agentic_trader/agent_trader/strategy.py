"""Strategies.

Each strategy precomputes its indicator series once via ``prepare()`` and then
answers ``evaluate(i, position)`` for one bar at a time. Because every series
element ``i`` is a function of ``bars[:i+1]`` only (see ``indicators``), the
precompute is not a lookahead: it is the same number the strategy would get if it
recomputed with history truncated at ``i``.

Strategies decide *direction and timing*. They never decide size and never veto --
sizing belongs to ``risk`` and vetoing belongs to ``gate``. Keeping those three
concerns apart is what makes each one testable in isolation.
"""

from __future__ import annotations

import abc
from collections.abc import Sequence

from agent_trader.config import StrategyConfig
from agent_trader.indicators import (
    atr_series,
    donchian,
    ema_series,
    sma,
    sma_series,
    stdev,
    volume_surge,
)
from agent_trader.types import Action, Bar, Position, Side, Signal


class Strategy(abc.ABC):
    """Base class. Subclasses implement ``_evaluate``."""

    name: str = "base"

    def __init__(self, config: StrategyConfig) -> None:
        self.config = config
        self.symbol = config.symbol
        self._bars: Sequence[Bar] = []
        self._atr: list[float | None] = []

    def prepare(self, bars: Sequence[Bar]) -> None:
        """Precompute indicator series for the whole history."""
        self._bars = bars
        self._atr = atr_series(bars, self.config.atr_period)
        self._prepare(bars)

    def _prepare(self, bars: Sequence[Bar]) -> None:  # pragma: no cover - hook
        """Subclass hook for additional series."""

    def atr_at(self, i: int) -> float | None:
        return self._atr[i] if 0 <= i < len(self._atr) else None

    @property
    def warmup(self) -> int:
        """Bars required before the strategy can emit anything."""
        return self.config.atr_period + 1

    def evaluate(self, i: int, position: Position | None) -> Signal:
        """Signal for bar ``i``, given the current position in this symbol."""
        bar = self._bars[i]
        if i < self.warmup:
            return Signal(self.symbol, bar.ts, Action.HOLD, Side.FLAT, bar.close, "warmup")
        return self._evaluate(i, position)

    @abc.abstractmethod
    def _evaluate(self, i: int, position: Position | None) -> Signal: ...

    def _hold(self, i: int, reason: str) -> Signal:
        bar = self._bars[i]
        return Signal(self.symbol, bar.ts, Action.HOLD, Side.FLAT, bar.close, reason,
                      self.atr_at(i))

    def _enter(self, i: int, side: Side, reason: str) -> Signal:
        bar = self._bars[i]
        return Signal(self.symbol, bar.ts, Action.ENTER, side, bar.close, reason,
                      self.atr_at(i))

    def _exit(self, i: int, reason: str) -> Signal:
        bar = self._bars[i]
        return Signal(self.symbol, bar.ts, Action.EXIT, Side.FLAT, bar.close, reason,
                      self.atr_at(i))


class MeanReversionStrategy(Strategy):
    """Bollinger reversion. Long below the lower band, short above the upper,
    exit when price returns to the mean.

    SPY runs at 1.5 sigma and QQQ at 1.8 -- QQQ is the more volatile index, so a
    wider band is needed for a touch to carry the same information.
    """

    name = "mean_reversion"

    def _prepare(self, bars: Sequence[Bar]) -> None:
        self._closes = [b.close for b in bars]
        self._mid = sma_series(self._closes, self.config.sma_period)

    @property
    def warmup(self) -> int:
        return max(self.config.sma_period, self.config.atr_period) + 1

    def _evaluate(self, i: int, position: Position | None) -> Signal:
        mid = self._mid[i]
        sd = stdev(self._closes, self.config.sma_period, i)
        if mid is None or sd is None:
            return self._hold(i, "no_band")
        price = self._closes[i]
        upper = mid + self.config.band_sigma * sd
        lower = mid - self.config.band_sigma * sd

        if position is not None:
            # Exit on reversion to the mean, direction-aware.
            if position.side is Side.LONG and price >= mid:
                return self._exit(i, "reverted_to_mean")
            if position.side is Side.SHORT and price <= mid:
                return self._exit(i, "reverted_to_mean")
            return self._hold(i, "holding")

        if sd == 0:
            # A flat window gives bands of zero width; every price "touches" both.
            return self._hold(i, "zero_volatility")
        if price < lower:
            return self._enter(i, Side.LONG, f"below_lower_band_{self.config.band_sigma}sigma")
        if price > upper:
            return self._enter(i, Side.SHORT, f"above_upper_band_{self.config.band_sigma}sigma")
        return self._hold(i, "inside_bands")


class MomentumBreakoutStrategy(Strategy):
    """Donchian breakout with volume confirmation.

    The channel is measured with ``shift=1`` so the current bar's own high is not
    part of the channel it must exceed. Volume must exceed its trailing average by
    ``volume_surge_multiple`` -- a breakout on thin volume is usually noise.
    Exits are handled by the engine's ATR trailing stop, not here.
    """

    name = "momentum_breakout"

    def _prepare(self, bars: Sequence[Bar]) -> None:
        self._volumes = [b.volume for b in bars]

    @property
    def warmup(self) -> int:
        return max(self.config.donchian_period + 1, self.config.atr_period) + 1

    def _evaluate(self, i: int, position: Position | None) -> Signal:
        if position is not None:
            return self._hold(i, "holding_trailing_stop")
        chan = donchian(self._bars, self.config.donchian_period, i, shift=1)
        if chan is None:
            return self._hold(i, "no_channel")
        low, high = chan
        price = self._bars[i].close
        if not volume_surge(self._volumes, self.config.sma_period,
                            self.config.volume_surge_multiple, i):
            if price > high or price < low:
                return self._hold(i, "breakout_without_volume")
            return self._hold(i, "inside_channel")
        if price > high:
            return self._enter(i, Side.LONG, "donchian_breakout_up_volume_confirmed")
        if price < low:
            return self._enter(i, Side.SHORT, "donchian_breakout_down_volume_confirmed")
        return self._hold(i, "inside_channel")


class TrendFollowingStrategy(Strategy):
    """EMA(50)/EMA(200) crossover.

    Entries fire on the *cross*, not on the state, so a position is opened once per
    regime change rather than re-opened on every bar the regime persists.
    """

    name = "trend_following"

    def _prepare(self, bars: Sequence[Bar]) -> None:
        closes = [b.close for b in bars]
        self._fast = ema_series(closes, self.config.fast_ema)
        self._slow = ema_series(closes, self.config.slow_ema)

    @property
    def warmup(self) -> int:
        return max(self.config.slow_ema, self.config.atr_period) + 1

    def _regime(self, i: int) -> Side | None:
        f, s = self._fast[i], self._slow[i]
        if f is None or s is None:
            return None
        if f > s:
            return Side.LONG
        if f < s:
            return Side.SHORT
        return None

    def _evaluate(self, i: int, position: Position | None) -> Signal:
        now, prev = self._regime(i), self._regime(i - 1)
        if now is None:
            return self._hold(i, "no_emas")

        if position is not None:
            if now is not position.side:
                return self._exit(i, "regime_reversal")
            return self._hold(i, "holding_trend")

        if prev is None or now is prev:
            return self._hold(i, "no_cross")
        label = "golden_cross" if now is Side.LONG else "death_cross"
        return self._enter(i, now, label)


_REGISTRY: dict[str, type[Strategy]] = {
    MeanReversionStrategy.name: MeanReversionStrategy,
    MomentumBreakoutStrategy.name: MomentumBreakoutStrategy,
    TrendFollowingStrategy.name: TrendFollowingStrategy,
}


def build_strategy(config: StrategyConfig) -> Strategy:
    """Instantiate the strategy named by ``config.name``."""
    try:
        cls = _REGISTRY[config.name]
    except KeyError:
        raise ValueError(
            f"unknown strategy {config.name!r}; known: {sorted(_REGISTRY)}"
        ) from None
    return cls(config)
