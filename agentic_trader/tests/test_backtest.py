"""Engine-level integration: accounting, the gate's effect, and halting."""

import tempfile
import unittest
from pathlib import Path

from agent_trader.backtest import Backtester, infer_periods_per_year
from agent_trader.config import (
    AppConfig,
    ExecutionConfig,
    GateConfig,
    RiskConfig,
    default_strategies,
)
from agent_trader.feeds import SyntheticFeed
from tests.helpers import ledger_for

STRATEGIES = default_strategies()


def _bars(seed=11, n=900, vol=0.45):
    feed = SyntheticFeed(seed=seed, n_bars=n, annual_vol=vol)
    return {s.symbol: feed.load(s.symbol, s.interval) for s in STRATEGIES}


def _run(conviction, threshold=0.50, root=None, bars=None, **risk_kw):
    bars = bars if bars is not None else _bars()
    cfg = AppConfig(
        risk=RiskConfig(**risk_kw),
        gate=GateConfig(veto_threshold=threshold),
        execution=ExecutionConfig(),
        strategies=STRATEGIES,
    )
    bt = Backtester(cfg, root=root or Path(tempfile.mkdtemp()))
    first = min(b[0].ts for b in bars.values())
    ledger = (ledger_for(list(bars), conviction, now=first)
              if conviction is not None else None)
    result, perf = bt.run(bars, ledger=ledger)
    return bt, result, perf


class TestAccounting(unittest.TestCase):
    """The books must balance exactly, or every reported number is suspect."""

    def test_ending_equity_equals_start_plus_sum_of_pnl(self):
        _, result, _ = _run(0.85)
        expected = result.starting_equity + sum(t.pnl for t in result.trades)
        self.assertAlmostEqual(result.ending_equity, expected, places=6)

    def test_run_produces_trades_to_make_the_identity_meaningful(self):
        _, result, _ = _run(0.85)
        self.assertGreater(len(result.trades), 0)

    def test_every_position_is_closed_by_the_end(self):
        _, result, _ = _run(0.85)
        self.assertEqual(result.equity_curve[-1].open_positions, 0)

    def test_final_mark_matches_ending_equity(self):
        _, result, _ = _run(0.85)
        self.assertAlmostEqual(result.equity_curve[-1].equity,
                               result.ending_equity, places=9)

    def test_performance_totals_agree_with_trades(self):
        _, result, perf = _run(0.85)
        self.assertEqual(perf.total_trades, len(result.trades))
        self.assertAlmostEqual(perf.total_pnl,
                               sum(t.pnl for t in result.trades), places=6)
        self.assertEqual(perf.wins + perf.losses, perf.total_trades)


class TestGateProof(unittest.TestCase):
    """The headline claim: below-threshold conviction trades nothing at all."""

    def test_low_conviction_executes_zero_trades(self):
        _, result, perf = _run(0.20, threshold=0.50)
        self.assertEqual(len(result.trades), 0)
        self.assertEqual(perf.total_trades, 0)

    def test_low_conviction_leaves_equity_exactly_untouched(self):
        _, result, _ = _run(0.20, threshold=0.50)
        self.assertEqual(result.ending_equity, 100_000.0)

    def test_denials_are_recorded_and_attributed_to_the_gate(self):
        _, result, _ = _run(0.20, threshold=0.50)
        self.assertTrue(result.denials)
        self.assertTrue(
            any("conviction_below_threshold" in r for r in result.denials),
            f"expected a gate denial, saw {list(result.denials)}",
        )

    def test_same_data_at_high_conviction_does_trade(self):
        """Without this, zero trades might just mean the strategies never fired."""
        bars = _bars()
        _, low, _ = _run(0.20, bars=bars)
        _, high, _ = _run(0.85, bars=bars)
        self.assertEqual(len(low.trades), 0)
        self.assertGreater(len(high.trades), 0)

    def test_no_ledger_at_all_denies_everything(self):
        _, result, _ = _run(None)
        self.assertEqual(len(result.trades), 0)

    def test_kill_switch_file_stops_all_trading(self):
        root = Path(tempfile.mkdtemp())
        (root / "shared" / "control").mkdir(parents=True, exist_ok=True)
        (root / "shared" / "control" / "KILL").touch()
        _, result, _ = _run(0.99, root=root)
        self.assertEqual(len(result.trades), 0)


class TestDrawdownHalt(unittest.TestCase):
    def test_tight_limit_halts_and_flattens(self):
        _, result, _ = _run(0.95, max_portfolio_drawdown=0.02)
        self.assertTrue(result.halted)
        self.assertIn("max_drawdown", result.halt_reason)
        self.assertEqual(result.equity_curve[-1].open_positions, 0)

    def test_halt_stops_further_entries(self):
        _, result, _ = _run(0.95, max_portfolio_drawdown=0.02)
        after = [t for t in result.trades if t.exit_reason == "drawdown_halt_liquidation"]
        self.assertLessEqual(len(after), 5)
        self.assertTrue(any("max_drawdown" in r for r in result.denials))

    def test_generous_limit_does_not_halt(self):
        _, result, _ = _run(0.85, max_portfolio_drawdown=0.95)
        self.assertFalse(result.halted)


class TestEquityCurve(unittest.TestCase):
    def test_marked_once_per_timestamp(self):
        """Five symbols sharing a timestamp must emit one mark, not five.

        Per-event marking would make the return series non-uniform in time and
        roughly double the inferred annualisation factor.
        """
        _, result, _ = _run(0.85)
        stamps = [p.ts for p in result.equity_curve]
        # The only repeat is the deliberate final post-flatten mark.
        self.assertLessEqual(len(stamps) - len(set(stamps)), 1)

    def test_curve_is_chronological(self):
        _, result, _ = _run(0.85)
        ts = [p.ts for p in result.equity_curve]
        self.assertEqual(ts, sorted(ts))

    def test_annualisation_is_inferred_not_hardcoded(self):
        _, _, perf = _run(0.85)
        self.assertNotEqual(perf.periods_per_year, 252)
        self.assertGreater(perf.periods_per_year, 252)


class TestSizingReport(unittest.TestCase):
    def test_binding_constraint_is_recorded(self):
        bt, result, _ = _run(0.85)
        self.assertTrue(bt.sizing_bounds)
        self.assertEqual(sum(bt.sizing_bounds.values()), len(result.trades))

    def test_notional_cap_binds_for_this_book(self):
        """Documents the real behaviour rather than the aspiration."""
        bt, _, _ = _run(0.85)
        self.assertIn("notional_cap", bt.sizing_bounds)


class TestEmptyAndDegenerate(unittest.TestCase):
    def test_no_bars_runs_without_error(self):
        cfg = AppConfig(strategies=STRATEGIES)
        bt = Backtester(cfg, root=Path(tempfile.mkdtemp()))
        result, perf = bt.run({})
        self.assertEqual(len(result.trades), 0)
        self.assertEqual(perf.total_trades, 0)

    def test_unconfigured_symbol_is_ignored(self):
        bars = {"NOT-IN-BOOK": SyntheticFeed(n_bars=100).load("NOT-IN-BOOK", "1h")}
        cfg = AppConfig(strategies=STRATEGIES)
        bt = Backtester(cfg, root=Path(tempfile.mkdtemp()))
        result, _ = bt.run(bars)
        self.assertEqual(len(result.trades), 0)

    def test_periods_per_year_falls_back_on_short_input(self):
        self.assertEqual(infer_periods_per_year([]), 252)


class TestDeterminism(unittest.TestCase):
    def test_same_inputs_give_identical_results(self):
        bars = _bars(seed=5)
        _, a, pa = _run(0.85, bars=bars)
        _, b, pb = _run(0.85, bars=bars)
        self.assertEqual(len(a.trades), len(b.trades))
        self.assertAlmostEqual(a.ending_equity, b.ending_equity, places=9)
        self.assertEqual(pa.sharpe, pb.sharpe)


class TestBenchmark(unittest.TestCase):
    """The benchmark must be computed, not assumed."""

    def test_flat_market_benchmark_is_zero(self):
        from agent_trader.benchmark import equal_weight_buy_and_hold
        from tests.helpers import bars_from_closes

        b = equal_weight_buy_and_hold({"A": bars_from_closes([100.0] * 10)}, 1000.0)
        self.assertAlmostEqual(b.total_return, 0.0, places=12)

    def test_doubling_market_doubles_equity(self):
        from agent_trader.benchmark import equal_weight_buy_and_hold
        from tests.helpers import bars_from_closes

        b = equal_weight_buy_and_hold({"A": bars_from_closes([100.0, 200.0])}, 1000.0)
        self.assertAlmostEqual(b.ending_equity, 2000.0, places=9)

    def test_equal_weight_splits_across_symbols(self):
        from agent_trader.benchmark import equal_weight_buy_and_hold
        from tests.helpers import bars_from_closes

        b = equal_weight_buy_and_hold(
            {"A": bars_from_closes([100.0, 200.0]),
             "B": bars_from_closes([100.0, 100.0])}, 1000.0)
        # 500 doubles to 1000, 500 stays -> 1500
        self.assertAlmostEqual(b.ending_equity, 1500.0, places=9)
        self.assertAlmostEqual(b.total_return, 0.5, places=9)

    def test_empty_input_returns_starting_equity(self):
        from agent_trader.benchmark import equal_weight_buy_and_hold

        self.assertEqual(equal_weight_buy_and_hold({}, 1000.0).ending_equity, 1000.0)

    def test_render_states_whether_strategy_beat_it(self):
        from agent_trader.benchmark import equal_weight_buy_and_hold
        from tests.helpers import bars_from_closes

        b = equal_weight_buy_and_hold({"A": bars_from_closes([100.0, 200.0])}, 1000.0)
        self.assertIn("LOST TO", b.render(strategy_return=-0.05))
        self.assertIn("BEAT", b.render(strategy_return=2.0))
