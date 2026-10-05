"""Strategy entry/exit behaviour."""

import unittest
from datetime import timedelta

from agent_trader.config import StrategyConfig
from agent_trader.strategy import build_strategy
from agent_trader.types import Action, Bar, Position, Side
from tests.helpers import T0, bars_from_closes


def _oscillating(n=40, base=100.0, amp=0.4):
    return [base + (amp if i % 2 else -amp) for i in range(n)]


class TestMeanReversion(unittest.TestCase):
    def _strat(self, sigma=1.5):
        return build_strategy(StrategyConfig("mean_reversion", "TEST", "15m",
                                             band_sigma=sigma))

    def test_long_below_lower_band(self):
        s = self._strat()
        bars = bars_from_closes(_oscillating() + [94.0])
        s.prepare(bars)
        sig = s.evaluate(len(bars) - 1, None)
        self.assertIs(sig.action, Action.ENTER)
        self.assertIs(sig.side, Side.LONG)

    def test_short_above_upper_band(self):
        s = self._strat()
        bars = bars_from_closes(_oscillating() + [106.0])
        s.prepare(bars)
        sig = s.evaluate(len(bars) - 1, None)
        self.assertIs(sig.action, Action.ENTER)
        self.assertIs(sig.side, Side.SHORT)

    def test_no_entry_inside_bands(self):
        s = self._strat()
        bars = bars_from_closes(_oscillating())
        s.prepare(bars)
        self.assertIs(s.evaluate(len(bars) - 1, None).action, Action.HOLD)

    def test_long_exits_once_price_reaches_the_mean(self):
        """Exit fires at ``price >= mid``, computed from the actual window.

        Asserting against a hardcoded 100.0 would be wrong: the oscillating series
        gives SMA20 = 100.02 over this window, so a close of exactly 100.0 has not
        yet reached the mean and HOLD is the correct answer. The exit boundary is
        derived here rather than guessed.
        """
        s = self._strat()
        base = _oscillating()
        probe = bars_from_closes(base + [100.0])
        s.prepare(probe)
        mid = s._mid[len(probe) - 1]
        self.assertIsNotNone(mid)

        # Just below the mean: still holding.
        below = bars_from_closes(base + [mid - 0.05])
        s.prepare(below)
        pos = Position("TEST", Side.LONG, 1, 94.0, T0, 1.0, 92.0, 94.0)
        self.assertIs(s.evaluate(len(below) - 1, pos).action, Action.HOLD)

        # At or above the mean: exit.
        above = bars_from_closes(base + [mid + 0.05])
        s.prepare(above)
        sig = s.evaluate(len(above) - 1, pos)
        self.assertIs(sig.action, Action.EXIT)
        self.assertEqual(sig.reason, "reverted_to_mean")

    def test_short_does_not_exit_above_mean(self):
        s = self._strat()
        bars = bars_from_closes(_oscillating() + [106.0])
        s.prepare(bars)
        pos = Position("TEST", Side.SHORT, 1, 106.0, T0, 1.0, 108.0, 106.0)
        self.assertIs(s.evaluate(len(bars) - 1, pos).action, Action.HOLD)

    def test_zero_volatility_blocks_entry(self):
        s = self._strat()
        bars = bars_from_closes([100.0] * 40)
        s.prepare(bars)
        sig = s.evaluate(len(bars) - 1, None)
        self.assertIs(sig.action, Action.HOLD)
        self.assertEqual(sig.reason, "zero_volatility")

    def test_wider_sigma_is_harder_to_trigger(self):
        bars = bars_from_closes(_oscillating() + [99.0])
        narrow, wide = self._strat(1.5), self._strat(4.0)
        narrow.prepare(bars)
        wide.prepare(bars)
        self.assertIs(narrow.evaluate(len(bars) - 1, None).action, Action.ENTER)
        self.assertIs(wide.evaluate(len(bars) - 1, None).action, Action.HOLD)

    def test_warmup_suppresses_signals(self):
        s = self._strat()
        bars = bars_from_closes(_oscillating())
        s.prepare(bars)
        self.assertEqual(s.evaluate(3, None).reason, "warmup")


class TestMomentumBreakout(unittest.TestCase):
    def _strat(self):
        return build_strategy(StrategyConfig("momentum_breakout", "TEST", "1h"))

    def test_breakout_with_volume_enters_long(self):
        bars = bars_from_closes([100.0] * 40)
        bars.append(Bar("TEST", T0 + timedelta(hours=40), 115, 115.5, 114.5, 115, 5000.0))
        s = self._strat()
        s.prepare(bars)
        sig = s.evaluate(len(bars) - 1, None)
        self.assertIs(sig.action, Action.ENTER)
        self.assertIs(sig.side, Side.LONG)

    def test_breakout_without_volume_is_refused(self):
        bars = bars_from_closes([100.0] * 40 + [115.0])
        s = self._strat()
        s.prepare(bars)
        sig = s.evaluate(len(bars) - 1, None)
        self.assertIs(sig.action, Action.HOLD)
        self.assertEqual(sig.reason, "breakout_without_volume")

    def test_breakdown_with_volume_enters_short(self):
        bars = bars_from_closes([100.0] * 40)
        bars.append(Bar("TEST", T0 + timedelta(hours=40), 85, 85.5, 84.5, 85, 5000.0))
        s = self._strat()
        s.prepare(bars)
        self.assertIs(s.evaluate(len(bars) - 1, None).side, Side.SHORT)

    def test_holds_while_in_position(self):
        bars = bars_from_closes([100.0] * 40)
        bars.append(Bar("TEST", T0 + timedelta(hours=40), 115, 115.5, 114.5, 115, 5000.0))
        s = self._strat()
        s.prepare(bars)
        pos = Position("TEST", Side.LONG, 1, 100.0, T0, 1.0, 98.0, 100.0)
        sig = s.evaluate(len(bars) - 1, pos)
        self.assertIs(sig.action, Action.HOLD)
        self.assertEqual(sig.reason, "holding_trailing_stop")


class TestTrendFollowing(unittest.TestCase):
    def _strat(self):
        return build_strategy(StrategyConfig("trend_following", "TEST", "4h",
                                             fast_ema=5, slow_ema=20))

    def test_golden_cross_fires_exactly_once(self):
        closes = [100 - i * 0.5 for i in range(60)] + [70 + i * 2.0 for i in range(60)]
        s = self._strat()
        bars = bars_from_closes(closes)
        s.prepare(bars)
        entries = [i for i in range(len(bars))
                   if s.evaluate(i, None).action is Action.ENTER]
        self.assertEqual(len(entries), 1, f"expected one cross, got {entries}")

    def test_cross_direction_is_long(self):
        closes = [100 - i * 0.5 for i in range(60)] + [70 + i * 2.0 for i in range(60)]
        s = self._strat()
        bars = bars_from_closes(closes)
        s.prepare(bars)
        entry = next(i for i in range(len(bars))
                     if s.evaluate(i, None).action is Action.ENTER)
        sig = s.evaluate(entry, None)
        self.assertIs(sig.side, Side.LONG)
        self.assertEqual(sig.reason, "golden_cross")

    def test_regime_reversal_exits(self):
        closes = [100 + i * 2.0 for i in range(60)] + [220 - i * 3.0 for i in range(60)]
        s = self._strat()
        bars = bars_from_closes(closes)
        s.prepare(bars)
        pos = Position("TEST", Side.LONG, 1, 150.0, T0, 1.0, 140.0, 150.0)
        exits = [i for i in range(len(bars))
                 if s.evaluate(i, pos).action is Action.EXIT]
        self.assertTrue(exits, "expected a regime-reversal exit")


class TestRegistry(unittest.TestCase):
    def test_unknown_strategy_raises_with_known_names(self):
        with self.assertRaises(ValueError) as ctx:
            build_strategy(StrategyConfig("nope", "TEST", "1h"))
        self.assertIn("mean_reversion", str(ctx.exception))
