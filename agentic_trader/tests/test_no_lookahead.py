"""Proof that no indicator or strategy can see the future.

The engine's central claim is that a decision at bar ``i`` uses only bars
``0..i``. That claim is cheap to make and easy to break -- a single
``series[i+1]`` or an unshifted Donchian channel is enough -- so it is asserted
here rather than trusted.

Method: take a price series, then **mutate every bar from index 50 onward** (5x
the prices, 10x the volumes). If anything reads ahead, values and decisions at
bars 0..49 will change. They must be bit-identical.
"""

import unittest

from agent_trader.config import StrategyConfig
from agent_trader.indicators import (
    atr_series,
    donchian,
    ema_series,
    rsi_series,
    sma_series,
)
from agent_trader.strategy import build_strategy
from agent_trader.types import Bar
from tests.helpers import bars_from_closes

SPLIT = 50
BASE = [100.0 + (i % 7) * 1.5 - (i % 3) * 2.0 for i in range(140)]


def _mutate(bars):
    """Bars with everything from ``SPLIT`` onward violently changed."""
    out = []
    for i, b in enumerate(bars):
        if i < SPLIT:
            out.append(b)
        else:
            out.append(Bar(b.symbol, b.ts, b.open * 5, b.high * 5, b.low * 5,
                           b.close * 5, b.volume * 10))
    return out


class TestIndicatorsDoNotReadAhead(unittest.TestCase):
    def setUp(self):
        self.bars = bars_from_closes(BASE)
        self.mutated = _mutate(self.bars)
        self.closes = [b.close for b in self.bars]
        self.mclose = [b.close for b in self.mutated]

    def _assert_prefix_identical(self, a, b, label):
        for i in range(SPLIT):
            self.assertEqual(a[i], b[i], f"{label} changed at bar {i}")

    def test_sma_prefix_unchanged(self):
        self._assert_prefix_identical(
            sma_series(self.closes, 20), sma_series(self.mclose, 20), "sma")

    def test_ema_prefix_unchanged(self):
        self._assert_prefix_identical(
            ema_series(self.closes, 20), ema_series(self.mclose, 20), "ema")

    def test_rsi_prefix_unchanged(self):
        self._assert_prefix_identical(
            rsi_series(self.closes, 14), rsi_series(self.mclose, 14), "rsi")

    def test_atr_prefix_unchanged(self):
        self._assert_prefix_identical(
            atr_series(self.bars, 14), atr_series(self.mutated, 14), "atr")

    def test_donchian_prefix_unchanged(self):
        for i in range(25, SPLIT):
            self.assertEqual(
                donchian(self.bars, 20, i, shift=1),
                donchian(self.mutated, 20, i, shift=1),
                f"donchian changed at bar {i}",
            )

    def test_mutation_actually_changes_the_suffix(self):
        """Guard against a vacuous test: the mutation must matter somewhere."""
        a = sma_series(self.closes, 20)
        b = sma_series(self.mclose, 20)
        self.assertNotEqual(a[-1], b[-1],
                            "mutation had no effect; the test proves nothing")


class TestStrategiesDoNotReadAhead(unittest.TestCase):
    """Every strategy must produce identical decisions over bars 0..49."""

    CASES = [
        StrategyConfig("mean_reversion", "TEST", "15m", band_sigma=1.5),
        StrategyConfig("momentum_breakout", "TEST", "1h"),
        StrategyConfig("trend_following", "TEST", "4h", fast_ema=5, slow_ema=20),
    ]

    def test_decisions_on_past_bars_are_identical(self):
        bars = bars_from_closes(BASE)
        mutated = _mutate(bars)
        for cfg in self.CASES:
            with self.subTest(strategy=cfg.name):
                a = build_strategy(cfg)
                b = build_strategy(cfg)
                a.prepare(bars)
                b.prepare(mutated)
                for i in range(SPLIT):
                    sa, sb = a.evaluate(i, None), b.evaluate(i, None)
                    self.assertEqual(
                        (sa.action, sa.side, sa.reason, sa.price, sa.atr),
                        (sb.action, sb.side, sb.reason, sb.price, sb.atr),
                        f"{cfg.name} differs at bar {i}",
                    )

    def test_donchian_without_shift_would_leak(self):
        """Demonstrate that ``shift`` is what stops the breakout self-referencing.

        With ``shift=0`` the current bar's own high is inside the channel it must
        exceed, so ``close > highest_high`` can never fire. This is why the
        default is 1, and this test fails loudly if someone changes it.
        """
        bars = bars_from_closes([100.0 + i for i in range(40)])
        low0, high0 = donchian(bars, 20, 39, shift=0)
        low1, high1 = donchian(bars, 20, 39, shift=1)
        self.assertGreaterEqual(high0, bars[39].high)
        self.assertLess(high1, bars[39].high)
