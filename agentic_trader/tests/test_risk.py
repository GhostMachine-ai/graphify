"""Position sizing and portfolio limits."""

import tempfile
import unittest
from pathlib import Path

from agent_trader.config import RiskConfig
from agent_trader.risk import RiskManager
from agent_trader.types import Position, Side
from tests.helpers import T0


def _pos(symbol, side=Side.LONG):
    return Position(symbol, side, 1.0, 100.0, T0, 1.0, 90.0, 100.0)


class TestSizing(unittest.TestCase):
    def setUp(self):
        self.rm = RiskManager(RiskConfig(starting_equity=100_000.0,
                                         risk_per_trade=0.01))

    def test_atr_target_risks_exactly_one_percent_when_it_binds(self):
        # High ATR relative to price keeps notional small, so the ATR target binds.
        d = self.rm.size_detail(100_000.0, atr=5.0, atr_multiple=2.0, price=20.0)
        self.assertEqual(d.binding_constraint, "atr_risk_target")
        self.assertAlmostEqual(d.implied_risk_dollars, 1_000.0, places=9)

    def test_notional_cap_binds_for_low_volatility_instruments(self):
        # GLD-like: ATR 0.80 at $190 would need ~$237k notional to risk $1k.
        d = self.rm.size_detail(100_000.0, atr=0.80, atr_multiple=2.0, price=190.0)
        self.assertEqual(d.binding_constraint, "notional_cap")
        self.assertAlmostEqual(d.notional, 20_000.0, places=6)
        self.assertLess(d.implied_risk_dollars, 1_000.0)

    def test_realised_risk_never_exceeds_target(self):
        for atr, price in [(0.8, 190.0), (2.5, 580.0), (1500.0, 95_000.0),
                           (5.0, 20.0), (0.05, 3.0)]:
            d = self.rm.size_detail(100_000.0, atr, 2.0, price)
            self.assertLessEqual(d.implied_risk_dollars, 1_000.0 + 1e-9,
                                 f"atr={atr} price={price}")

    def test_zero_atr_returns_no_position(self):
        d = self.rm.size_detail(100_000.0, 0.0, 2.0, 100.0)
        self.assertEqual(d.units, 0.0)
        self.assertEqual(d.binding_constraint, "invalid_inputs")

    def test_nonpositive_inputs_return_no_position(self):
        for kwargs in [dict(atr=-1.0, atr_multiple=2.0, price=100.0),
                       dict(atr=1.0, atr_multiple=0.0, price=100.0),
                       dict(atr=1.0, atr_multiple=2.0, price=0.0)]:
            self.assertEqual(self.rm.size_detail(100_000.0, **kwargs).units, 0.0)

    def test_zero_equity_returns_no_position(self):
        self.assertEqual(self.rm.size_detail(0.0, 1.0, 2.0, 100.0).units, 0.0)

    def test_position_size_matches_detail(self):
        self.assertEqual(
            self.rm.position_size(100_000.0, 2.0, 2.0, 100.0),
            self.rm.size_detail(100_000.0, 2.0, 2.0, 100.0).units,
        )


class TestCorrelationFilter(unittest.TestCase):
    def setUp(self):
        self.rm = RiskManager(RiskConfig())
        self.both = {"SPY": _pos("SPY"), "QQQ": _pos("QQQ")}

    def test_blocks_btc_long_when_spy_and_qqq_long(self):
        d = self.rm.correlation_block("BTC-USD", Side.LONG, self.both)
        self.assertFalse(d.allowed)
        self.assertIn("correlation_block", d.reason)

    def test_allows_btc_long_with_only_one_leg(self):
        self.assertTrue(
            self.rm.correlation_block("BTC-USD", Side.LONG, {"SPY": _pos("SPY")}).allowed
        )

    def test_allows_btc_short_regardless(self):
        self.assertTrue(
            self.rm.correlation_block("BTC-USD", Side.SHORT, self.both).allowed
        )

    def test_ignores_short_equity_legs(self):
        mixed = {"SPY": _pos("SPY", Side.LONG), "QQQ": _pos("QQQ", Side.SHORT)}
        self.assertTrue(self.rm.correlation_block("BTC-USD", Side.LONG, mixed).allowed)

    def test_other_symbols_unconstrained(self):
        self.assertTrue(self.rm.correlation_block("GLD", Side.LONG, self.both).allowed)

    def test_disabled_filter_permits(self):
        rm = RiskManager(RiskConfig(correlation_filter_enabled=False))
        self.assertTrue(rm.correlation_block("BTC-USD", Side.LONG, self.both).allowed)


class TestDrawdownBreaker(unittest.TestCase):
    def test_halts_at_threshold(self):
        rm = RiskManager(RiskConfig(max_portfolio_drawdown=0.10))
        rm.state.mark(100_000.0)
        rm.state.mark(89_000.0)
        d = rm.check_drawdown()
        self.assertFalse(d.allowed)
        self.assertTrue(rm.state.halted)

    def test_does_not_halt_just_inside(self):
        rm = RiskManager(RiskConfig(max_portfolio_drawdown=0.10))
        rm.state.mark(100_000.0)
        rm.state.mark(90_500.0)
        self.assertTrue(rm.check_drawdown().allowed)
        self.assertFalse(rm.state.halted)

    def test_halt_is_sticky_even_after_recovery(self):
        rm = RiskManager(RiskConfig(max_portfolio_drawdown=0.10))
        rm.state.mark(100_000.0)
        rm.state.mark(80_000.0)
        rm.check_drawdown()
        rm.state.mark(120_000.0)
        self.assertFalse(rm.can_open("SPY", Side.LONG, {}).allowed)

    def test_peak_ratchets_upward(self):
        rm = RiskManager(RiskConfig())
        rm.state.mark(120_000.0)
        rm.state.mark(110_000.0)
        self.assertEqual(rm.state.peak_equity, 120_000.0)
        self.assertAlmostEqual(rm.state.drawdown, 10 / 120, places=9)


class TestGuards(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.rm = RiskManager(RiskConfig(max_concurrent_positions=2),
                              root=self.root)

    def test_kill_switch_file_blocks_entry(self):
        self.assertTrue(self.rm.can_open("SPY", Side.LONG, {}).allowed)
        (self.root / "shared" / "control").mkdir(parents=True, exist_ok=True)
        (self.root / "shared" / "control" / "KILL").touch()
        d = self.rm.can_open("SPY", Side.LONG, {})
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason, "kill_switch_engaged")

    def test_rejects_duplicate_position(self):
        d = self.rm.can_open("SPY", Side.LONG, {"SPY": _pos("SPY")})
        self.assertEqual(d.reason, "already_in_position")

    def test_enforces_max_concurrent(self):
        held = {"GLD": _pos("GLD"), "USO": _pos("USO")}
        d = self.rm.can_open("SPY", Side.LONG, held)
        self.assertEqual(d.reason, "max_concurrent_positions")


class TestStops(unittest.TestCase):
    def setUp(self):
        self.rm = RiskManager(RiskConfig())

    def test_initial_stop_is_adverse(self):
        self.assertAlmostEqual(
            self.rm.initial_stop(Side.LONG, 100.0, 2.0, 2.0), 96.0, places=9)
        self.assertAlmostEqual(
            self.rm.initial_stop(Side.SHORT, 100.0, 2.0, 2.0), 104.0, places=9)

    def test_long_trail_never_loosens(self):
        p = Position("T", Side.LONG, 1, 100.0, T0, 2.0, 96.0, 100.0)
        p.extreme_price = 110.0
        p.stop_price = self.rm.trail_stop(p, 2.0)
        self.assertAlmostEqual(p.stop_price, 106.0, places=9)
        p.extreme_price = 104.0
        self.assertAlmostEqual(self.rm.trail_stop(p, 2.0), 106.0, places=9)

    def test_short_trail_never_loosens(self):
        p = Position("T", Side.SHORT, 1, 100.0, T0, 2.0, 104.0, 100.0)
        p.extreme_price = 90.0
        p.stop_price = self.rm.trail_stop(p, 2.0)
        self.assertAlmostEqual(p.stop_price, 94.0, places=9)
        p.extreme_price = 96.0
        self.assertAlmostEqual(self.rm.trail_stop(p, 2.0), 94.0, places=9)
