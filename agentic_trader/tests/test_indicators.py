"""Indicator math, checked against values computed by hand."""

import math
import unittest

from agent_trader.indicators import (
    atr_series,
    bollinger,
    donchian,
    ema,
    ema_series,
    rsi_series,
    sma,
    sma_series,
    stdev,
    true_range,
    volume_surge,
)
from tests.helpers import bars_from_closes

V = [1.0, 2, 3, 4, 5, 6, 7, 8, 9, 10]


class TestSMA(unittest.TestCase):
    def test_known_values(self):
        self.assertEqual(sma(V, 3, 2), 2.0)
        self.assertEqual(sma(V, 3, 9), 9.0)

    def test_none_before_window_fills(self):
        self.assertIsNone(sma(V, 3, 1))
        self.assertIsNone(sma(V, 3, -1))

    def test_rejects_nonpositive_period(self):
        with self.assertRaises(ValueError):
            sma(V, 0, 5)

    def test_series_matches_pointwise(self):
        series = sma_series(V, 3)
        for i in range(len(V)):
            self.assertEqual(series[i], sma(V, 3, i), f"index {i}")


class TestStdev(unittest.TestCase):
    def test_population_not_sample(self):
        # population stdev of [1,2,3] is sqrt(2/3); sample would be 1.0
        self.assertAlmostEqual(stdev(V, 3, 2), math.sqrt(2 / 3), places=12)

    def test_zero_for_flat_window(self):
        self.assertEqual(stdev([5.0] * 10, 5, 9), 0.0)


class TestEMA(unittest.TestCase):
    def test_seeding_and_recurrence(self):
        # seed = SMA(1,2,3) = 2; k = 2/4 = 0.5
        s = ema_series(V, 3)
        self.assertEqual(s[2], 2.0)
        self.assertEqual(s[3], 3.0)
        self.assertEqual(s[4], 4.0)

    def test_none_before_seed(self):
        self.assertIsNone(ema_series(V, 3)[1])

    def test_pointwise_matches_series(self):
        self.assertEqual(ema(V, 3, 4), ema_series(V, 3)[4])

    def test_short_input(self):
        self.assertEqual(ema_series([1.0, 2.0], 5), [None, None])


class TestATR(unittest.TestCase):
    def test_constant_range_converges_to_range(self):
        bars = bars_from_closes([100.0] * 30, wick=1.0)  # high-low span of 2
        series = atr_series(bars, 14)
        self.assertAlmostEqual(series[13], 2.0, places=12)
        self.assertAlmostEqual(series[29], 2.0, places=12)

    def test_true_range_uses_prev_close_gaps(self):
        bars = bars_from_closes([100.0, 120.0], wick=0.5)
        # gap up: |high - prev_close| = 120.5 - 100 = 20.5 dominates the 1.0 span
        self.assertAlmostEqual(true_range(bars[1], bars[0].close), 20.5, places=12)

    def test_first_bar_falls_back_to_span(self):
        bars = bars_from_closes([100.0], wick=1.5)
        self.assertAlmostEqual(true_range(bars[0], None), 3.0, places=12)


class TestBollinger(unittest.TestCase):
    def test_zero_volatility_collapses_bands(self):
        self.assertEqual(bollinger([10.0] * 20, 20, 2.0, 19), (10.0, 10.0, 10.0))

    def test_sigma_scales_width(self):
        closes = [10.0, 12] * 10
        narrow = bollinger(closes, 20, 1.0, 19)
        wide = bollinger(closes, 20, 2.0, 19)
        self.assertLess(wide[0], narrow[0])
        self.assertGreater(wide[2], narrow[2])
        self.assertAlmostEqual(narrow[1], wide[1], places=12)


class TestDonchian(unittest.TestCase):
    def setUp(self):
        self.bars = bars_from_closes([100.0 + i for i in range(25)], wick=0.5)

    def test_shift_one_excludes_current_bar(self):
        # window is bars 4..23 -> highs 104.5..123.5, lows 103.5..122.5
        self.assertEqual(donchian(self.bars, 20, 24, shift=1), (103.5, 123.5))

    def test_shift_zero_includes_current_bar(self):
        self.assertEqual(donchian(self.bars, 20, 24, shift=0), (104.5, 124.5))

    def test_none_when_window_short(self):
        self.assertIsNone(donchian(self.bars, 20, 5, shift=1))

    def test_rejects_negative_shift(self):
        with self.assertRaises(ValueError):
            donchian(self.bars, 20, 24, shift=-1)


class TestRSI(unittest.TestCase):
    def test_all_gains_is_100(self):
        self.assertEqual(rsi_series([float(i) for i in range(1, 40)], 14)[14], 100.0)

    def test_all_losses_is_zero(self):
        self.assertAlmostEqual(
            rsi_series([float(i) for i in range(40, 1, -1)], 14)[14], 0.0, places=9
        )

    def test_none_before_period(self):
        self.assertIsNone(rsi_series([float(i) for i in range(1, 40)], 14)[13])


class TestVolumeSurge(unittest.TestCase):
    def test_detects_surge(self):
        self.assertTrue(volume_surge([100.0] * 20 + [400.0], 20, 1.5, 20))

    def test_flat_volume_is_not_a_surge(self):
        self.assertFalse(volume_surge([100.0] * 21, 20, 1.5, 20))

    def test_baseline_excludes_current_bar(self):
        # A single huge bar must not inflate the baseline it is compared against.
        self.assertTrue(volume_surge([100.0] * 20 + [10_000.0], 20, 1.5, 20))

    def test_false_when_window_short(self):
        self.assertFalse(volume_surge([100.0, 200.0], 20, 1.5, 1))
