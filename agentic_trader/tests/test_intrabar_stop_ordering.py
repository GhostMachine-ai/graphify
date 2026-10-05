"""Regression tests for the intra-bar trailing-stop ordering.

These exist because of a real bug that the rest of the suite could not see. The
engine used to ratchet the trailing stop with a bar's own high and then test that
raised stop against the same bar's low. That assumes the high occurred before the
low, which OHLC does not record.

The bug was worth **9.15 percentage points** on the real-data backtest and
inverted the sign of the project's headline result. Critically, the full suite
passed with *either* ordering: `test_execution.py` tests `update_extreme` and
`stop_hit` in isolation, `test_risk.py` tests `trail_stop` in isolation, and
nothing tested their composition inside the engine loop. These tests close that
hole.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from agent_trader.backtest import Backtester
from agent_trader.config import (
    AppConfig,
    ExecutionConfig,
    GateConfig,
    RiskConfig,
    StrategyConfig,
)
from agent_trader.types import Action, Bar, Side, Signal
from tests.helpers import T0, bars_from_closes, ledger_for


class TestEngineOrderingIsPinnedByGoldenValues(unittest.TestCase):
    """Characterisation test over the committed bars.

    This is the only test in the suite that actually discriminates between the
    two orderings, and it took two attempts to get right.

    The first attempt asserted "a long `stop_hit` exit cannot fill above its
    entry price". That is simply false: a *trailing* stop ratchets up, so a
    profitable stop-out is normal and 8 of 32 long stop exits on this data do
    exactly that. The second attempt tested `stop_hit` and `update_extreme` in
    the right order by hand -- which passes under either engine ordering,
    reproducing the very blind spot that let the bug through.

    What actually discriminates is the end-to-end result on frozen data. The
    bars in `data/cache/` are committed, so these numbers are reproducible:

        ordering                 trades   ending equity
        test-then-ratchet (ok)      140    103,256.36
        ratchet-then-test (bug)     144     94,106.21

    If a future change alters the engine's intra-bar ordering, this fails.
    """

    EXPECTED = {
        0.95: (140, 103256.36296826943),
        0.10: (88, 94941.60327090265),
    }

    def _run(self, max_dd):
        from agent_trader.config import daily_book
        from agent_trader.feeds import CachedFeed

        root = Path(__file__).resolve().parent.parent
        book = daily_book()
        feed = CachedFeed(root / "data" / "cache")
        bars = {s.symbol: feed.load(s.symbol, s.interval) for s in book}
        cfg = AppConfig(
            risk=RiskConfig(max_portfolio_drawdown=max_dd),
            gate=GateConfig(veto_threshold=0.50),
            strategies=book,
        )
        bt = Backtester(cfg, root=root)
        first = min(b[0].ts for b in bars.values())
        return bt.run(bars, ledger=ledger_for(list(bars), 0.85, now=first))

    def test_golden_values_hold_for_both_drawdown_settings(self):
        for max_dd, (trades, equity) in self.EXPECTED.items():
            with self.subTest(max_drawdown=max_dd):
                result, _ = self._run(max_dd)
                self.assertEqual(len(result.trades), trades)
                self.assertAlmostEqual(result.ending_equity, equity, places=6)

    def test_accounting_identity_still_holds_after_the_fix(self):
        result, _ = self._run(0.95)
        self.assertAlmostEqual(
            result.ending_equity,
            result.starting_equity + sum(t.pnl for t in result.trades),
            places=6,
        )


class TestStopSemanticsAtUnitLevel(unittest.TestCase):
    """The component behaviour the engine must compose correctly."""

    def test_pre_bar_stop_is_what_a_wide_bar_is_tested_against(self):
        from agent_trader.execution import ExecutionEngine
        from agent_trader.risk import RiskManager
        from agent_trader.types import Position

        e = ExecutionEngine(ExecutionConfig(slippage_bps=0.0, fee_bps=0.0))
        rm = RiskManager(RiskConfig())
        pos = Position("T", Side.LONG, 100.0, 100.0, T0, 1.0, 98.0, 100.0)
        bar = Bar("T", T0, 109.0, 110.0, 99.0, 109.5, 1000.0)

        hit, _, _ = e.stop_hit(pos, bar)
        self.assertFalse(hit, "low 99 is above the pre-bar stop 98")

        e.update_extreme(pos, bar)
        pos.stop_price = rm.trail_stop(pos, 2.0)
        self.assertAlmostEqual(pos.stop_price, 108.0, places=9)

        nxt = Bar("T", T0 + timedelta(days=1), 109.0, 109.5, 107.0, 107.5, 1.0)
        hit2, ref2, _ = e.stop_hit(pos, nxt)
        self.assertTrue(hit2, "the raised stop must fire on a later bar")
        self.assertAlmostEqual(ref2, 108.0, places=9)

    def test_ratchet_still_happens_so_the_trail_is_not_disabled(self):
        """Guard against 'fixing' the bug by never trailing at all."""
        from agent_trader.execution import ExecutionEngine
        from agent_trader.risk import RiskManager
        from agent_trader.types import Position

        e = ExecutionEngine(ExecutionConfig(slippage_bps=0.0, fee_bps=0.0))
        rm = RiskManager(RiskConfig())
        pos = Position("T", Side.LONG, 1.0, 100.0, T0, 1.0, 98.0, 100.0)
        for i in range(5):
            bar = Bar("T", T0 + timedelta(days=i), 100.0 + i, 101.0 + i,
                      99.5 + i, 100.5 + i, 1.0)
            if not e.stop_hit(pos, bar)[0]:
                e.update_extreme(pos, bar)
                pos.stop_price = rm.trail_stop(pos, 2.0)
        self.assertGreater(pos.stop_price, 98.0, "the trail must still ratchet")


class TestEquityCurveHasNoDuplicateTimestamp(unittest.TestCase):
    def test_final_mark_replaces_rather_than_duplicates(self):
        """A duplicated final timestamp is a return over zero elapsed time."""
        from agent_trader.config import daily_book
        from agent_trader.feeds import CachedFeed

        root = Path(__file__).resolve().parent.parent
        book = daily_book()
        feed = CachedFeed(root / "data" / "cache")
        bars = {s.symbol: feed.load(s.symbol, s.interval) for s in book}

        cfg = AppConfig(risk=RiskConfig(), gate=GateConfig(veto_threshold=0.5),
                        strategies=book)
        bt = Backtester(cfg, root=root)
        first = min(b[0].ts for b in bars.values())
        result, _ = bt.run(bars, ledger=ledger_for(list(bars), 0.85, now=first))

        stamps = [p.ts for p in result.equity_curve]
        self.assertEqual(len(stamps), len(set(stamps)),
                         "every equity mark must sit at a distinct timestamp")
        self.assertAlmostEqual(result.equity_curve[-1].equity,
                               result.ending_equity, places=9)


class TestSizingGuards(unittest.TestCase):
    """Non-finite and underflow inputs must not produce a sized position."""

    def setUp(self):
        from agent_trader.risk import RiskManager
        self.rm = RiskManager(RiskConfig())

    def test_product_underflow_does_not_divide_by_zero(self):
        # Both factors positive, product exactly 0.0.
        for atr, mult in [(5e-324, 1e-9), (1e-320, 1e-9), (1e-200, 1e-200)]:
            d = self.rm.size_detail(100_000.0, atr, mult, 100.0)
            self.assertEqual(d.units, 0.0, f"atr={atr} mult={mult}")
            self.assertEqual(d.binding_constraint, "invalid_inputs")

    def test_nan_inputs_return_no_position(self):
        nan = float("nan")
        for kwargs in [dict(atr=nan, atr_multiple=2.0, price=100.0),
                       dict(atr=1.0, atr_multiple=nan, price=100.0),
                       dict(atr=1.0, atr_multiple=2.0, price=nan)]:
            d = self.rm.size_detail(100_000.0, **kwargs)
            self.assertEqual(d.units, 0.0, kwargs)
            self.assertEqual(d.binding_constraint, "invalid_inputs")

    def test_nan_equity_returns_no_position(self):
        d = self.rm.size_detail(float("nan"), 1.0, 2.0, 100.0)
        self.assertEqual(d.units, 0.0)

    def test_infinite_inputs_return_no_position(self):
        inf = float("inf")
        for kwargs in [dict(atr=inf, atr_multiple=2.0, price=100.0),
                       dict(atr=1.0, atr_multiple=inf, price=100.0)]:
            self.assertEqual(self.rm.size_detail(100_000.0, **kwargs).units, 0.0)

    def test_nan_atr_is_refused_before_sizing(self):
        """_consider_entry must reject NaN too; `nan <= 0` is False."""
        cfg = AppConfig(
            risk=RiskConfig(), gate=GateConfig(enabled=False),
            strategies=[StrategyConfig("mean_reversion", "T", "1d")],
        )
        bt = Backtester(cfg, root=Path(tempfile.mkdtemp()))
        from agent_trader.types import BacktestResult
        res = BacktestResult(starting_equity=100_000.0)
        sig = Signal("T", T0, Action.ENTER, Side.LONG, 100.0, "x",
                     atr=float("nan"))
        pending: dict = {}
        bt._consider_entry(sig, cfg.strategies[0],
                           type("S", (), {"name": "mean_reversion"})(),
                           T0, {}, pending, res, None)
        self.assertEqual(pending, {}, "a NaN ATR must not queue an entry")
        self.assertIn("no_atr", res.denials)


class TestStrategyConfigValidation(unittest.TestCase):
    def test_nonpositive_stop_multiple_rejected(self):
        for v in (0, -5):
            with self.assertRaises(ValueError):
                StrategyConfig("mean_reversion", "T", "1d", atr_stop_multiple=v)

    def test_nonpositive_periods_rejected(self):
        for field in ("sma_period", "donchian_period", "atr_period"):
            with self.assertRaises(ValueError):
                StrategyConfig("mean_reversion", "T", "1d", **{field: 0})

    def test_inverted_emas_rejected(self):
        with self.assertRaises(ValueError):
            StrategyConfig("trend_following", "T", "1d", fast_ema=200,
                           slow_ema=50)

    def test_valid_config_still_constructs(self):
        self.assertEqual(
            StrategyConfig("mean_reversion", "T", "1d").atr_stop_multiple, 2.0)
