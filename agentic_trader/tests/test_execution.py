"""Fill simulation: slippage direction, fees, and stop resolution."""

import unittest

from agent_trader.config import ExecutionConfig
from agent_trader.execution import ExecutionEngine
from agent_trader.types import Bar, Position, Side
from tests.helpers import T0


class TestSlippage(unittest.TestCase):
    def setUp(self):
        self.e = ExecutionEngine(ExecutionConfig(slippage_bps=10.0, fee_bps=0.0))

    def test_slippage_always_hurts(self):
        # Opening a long is a buy -> pay more. Opening a short is a sell -> get less.
        self.assertGreater(self.e.slipped_price(100.0, Side.LONG, opening=True), 100.0)
        self.assertLess(self.e.slipped_price(100.0, Side.SHORT, opening=True), 100.0)
        # Closing reverses which direction is a buy.
        self.assertLess(self.e.slipped_price(100.0, Side.LONG, opening=False), 100.0)
        self.assertGreater(self.e.slipped_price(100.0, Side.SHORT, opening=False), 100.0)

    def test_magnitude_matches_bps(self):
        self.assertAlmostEqual(
            self.e.slipped_price(100.0, Side.LONG, opening=True), 100.10, places=9)

    def test_zero_slippage_is_identity(self):
        e = ExecutionEngine(ExecutionConfig(slippage_bps=0.0, fee_bps=0.0))
        self.assertEqual(e.slipped_price(100.0, Side.LONG, opening=True), 100.0)


class TestFees(unittest.TestCase):
    def test_fee_scales_with_notional(self):
        e = ExecutionEngine(ExecutionConfig(slippage_bps=0.0, fee_bps=10.0))
        self.assertAlmostEqual(e.fee_for(100.0, 10.0), 1.0, places=9)

    def test_fee_is_never_negative_for_short_units(self):
        e = ExecutionEngine(ExecutionConfig(fee_bps=10.0))
        self.assertGreater(e.fee_for(100.0, -10.0), 0.0)


class TestStops(unittest.TestCase):
    def setUp(self):
        self.e = ExecutionEngine(ExecutionConfig(slippage_bps=0.0, fee_bps=0.0))

    def _long(self, stop=96.0):
        return Position("T", Side.LONG, 1.0, 100.0, T0, 2.0, stop, 100.0)

    def test_long_stop_hit_intrabar_fills_at_stop(self):
        bar = Bar("T", T0, 99.0, 99.5, 95.0, 98.0, 100.0)
        hit, ref, why = self.e.stop_hit(self._long(), bar)
        self.assertTrue(hit)
        self.assertEqual(ref, 96.0)
        self.assertEqual(why, "stop_hit")

    def test_long_gap_through_stop_fills_at_open_not_stop(self):
        # Opens at 90, far below the 96 stop. A real stop becomes a market order.
        bar = Bar("T", T0, 90.0, 91.0, 89.0, 90.5, 100.0)
        hit, ref, why = self.e.stop_hit(self._long(), bar)
        self.assertTrue(hit)
        self.assertEqual(ref, 90.0, "gap must fill at the open, which is worse")
        self.assertEqual(why, "stop_gap_through_open")

    def test_long_stop_not_hit(self):
        bar = Bar("T", T0, 100.0, 101.0, 97.0, 100.5, 100.0)
        self.assertFalse(self.e.stop_hit(self._long(), bar)[0])

    def test_short_stop_hit_intrabar(self):
        pos = Position("T", Side.SHORT, 1.0, 100.0, T0, 2.0, 104.0, 100.0)
        bar = Bar("T", T0, 101.0, 105.0, 100.5, 102.0, 100.0)
        hit, ref, why = self.e.stop_hit(pos, bar)
        self.assertTrue(hit)
        self.assertEqual(ref, 104.0)

    def test_short_gap_through_stop_fills_at_open(self):
        pos = Position("T", Side.SHORT, 1.0, 100.0, T0, 2.0, 104.0, 100.0)
        bar = Bar("T", T0, 110.0, 111.0, 109.0, 110.5, 100.0)
        hit, ref, why = self.e.stop_hit(pos, bar)
        self.assertEqual(ref, 110.0)
        self.assertEqual(why, "stop_gap_through_open")

    def test_update_extreme_tracks_favourable_direction(self):
        p = self._long()
        self.e.update_extreme(p, Bar("T", T0, 100, 110, 99, 105, 1.0))
        self.assertEqual(p.extreme_price, 110.0)
        self.e.update_extreme(p, Bar("T", T0, 105, 107, 104, 106, 1.0))
        self.assertEqual(p.extreme_price, 110.0, "extreme must not retreat")

    def test_update_extreme_for_short_tracks_lows(self):
        p = Position("T", Side.SHORT, 1.0, 100.0, T0, 2.0, 104.0, 100.0)
        self.e.update_extreme(p, Bar("T", T0, 100, 101, 90, 95, 1.0))
        self.assertEqual(p.extreme_price, 90.0)


class TestClosePosition(unittest.TestCase):
    def test_pnl_includes_both_fees_cash_delta_only_exit(self):
        e = ExecutionEngine(ExecutionConfig(slippage_bps=0.0, fee_bps=10.0))
        p = Position("T", Side.LONG, 10.0, 100.0, T0, 2.0, 96.0, 100.0)
        trade, cash_delta = e.close_position(p, 110.0, T0, "target", entry_fee=1.0)
        exit_fee = e.fee_for(110.0, 10.0)
        self.assertAlmostEqual(cash_delta, 100.0 - exit_fee, places=9)
        self.assertAlmostEqual(trade.pnl, 100.0 - exit_fee - 1.0, places=9)
        self.assertAlmostEqual(trade.fees, exit_fee + 1.0, places=9)

    def test_short_profits_when_price_falls(self):
        e = ExecutionEngine(ExecutionConfig(slippage_bps=0.0, fee_bps=0.0))
        p = Position("T", Side.SHORT, 10.0, 100.0, T0, 2.0, 104.0, 100.0)
        trade, _ = e.close_position(p, 90.0, T0, "target")
        self.assertAlmostEqual(trade.pnl, 100.0, places=9)
        self.assertTrue(trade.is_win)

    def test_losing_trade_is_not_a_win(self):
        e = ExecutionEngine(ExecutionConfig(slippage_bps=0.0, fee_bps=0.0))
        p = Position("T", Side.LONG, 1.0, 100.0, T0, 2.0, 96.0, 100.0)
        trade, _ = e.close_position(p, 95.0, T0, "stop")
        self.assertFalse(trade.is_win)
