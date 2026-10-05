"""End-to-end CLI tests.

These run the package as a subprocess (``python3 -m agent_trader ...``) rather
than calling ``main()`` in-process, so they cover what a user actually invokes:
argument parsing, exit codes, and stdout. An in-process call would not catch a
broken ``__main__.py`` or a bad exit status.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = PKG_ROOT


def run_cli(*args: str, root: Path | None = None) -> subprocess.CompletedProcess:
    """Invoke the CLI as a subprocess and capture everything."""
    cmd = [sys.executable, "-m", "agent_trader", "--root", str(root or PKG_ROOT), *args]
    return subprocess.run(
        cmd, cwd=str(PKG_ROOT), capture_output=True, text=True, timeout=180
    )


def _seed_cache(root: Path, symbols: dict[str, str], n: int = 400) -> None:
    """Write synthetic bars into ``root`` as if they had been fetched."""
    sys.path.insert(0, str(PKG_ROOT))
    from agent_trader.feeds.cached import write_cache
    from agent_trader.feeds.synthetic import SyntheticFeed

    feed = SyntheticFeed(seed=4, n_bars=n, annual_vol=0.4)
    for symbol, interval in symbols.items():
        bars = feed.load(symbol, "1d")
        write_cache(
            root / "data" / "cache", symbol, interval,
            [{"ts": b.ts.isoformat(), "open": b.open, "high": b.high,
              "low": b.low, "close": b.close, "volume": b.volume} for b in bars],
            source="unit-test",
        )


class TestHelpAndErrors(unittest.TestCase):
    def test_bare_invocation_requires_a_subcommand(self):
        p = run_cli()
        self.assertNotEqual(p.returncode, 0)

    def test_help_exits_zero_and_states_the_disclaimer(self):
        p = run_cli("--help")
        self.assertEqual(p.returncode, 0)
        # argparse hard-wraps the description, so collapse whitespace before
        # matching rather than asserting against a particular line break.
        flat = " ".join(p.stdout.lower().split())
        self.assertIn("not investment advice", flat)
        self.assertIn("submits no orders", flat)

    def test_unknown_subcommand_fails(self):
        self.assertNotEqual(run_cli("frobnicate").returncode, 0)


class TestGateDemo(unittest.TestCase):
    def test_runs_and_shows_both_outcomes(self):
        p = run_cli("gate-demo")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("ALLOW", p.stdout)
        self.assertIn("DENY", p.stdout)

    def test_every_failure_mode_denies(self):
        out = run_cli("gate-demo").stdout
        for line_start in ["low conviction", "stale", "future as_of",
                           "malformed", "no signal"]:
            line = next(ln for ln in out.splitlines() if ln.startswith(line_start))
            self.assertIn("DENY", line, f"{line_start!r} should deny: {line}")


class TestCacheStatus(unittest.TestCase):
    def test_empty_cache_reports_and_exits_nonzero(self):
        root = Path(tempfile.mkdtemp())
        p = run_cli("cache-status", root=root)
        self.assertEqual(p.returncode, 1)
        self.assertIn("docs/DATA.md", p.stdout)

    def test_populated_cache_lists_symbols(self):
        root = Path(tempfile.mkdtemp())
        _seed_cache(root, {"SPY": "1d"}, n=50)
        p = run_cli("cache-status", root=root)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("SPY", p.stdout)

    def test_real_committed_cache_is_readable(self):
        """The committed bars must load through the CLI, not just in tests."""
        p = run_cli("cache-status")
        self.assertEqual(p.returncode, 0, p.stderr)
        for sym in ["SPY", "QQQ", "GLD", "USO", "IBIT"]:
            self.assertIn(sym, p.stdout)


class TestBacktestCommand(unittest.TestCase):
    def test_missing_data_exits_two_with_guidance(self):
        root = Path(tempfile.mkdtemp())
        p = run_cli("backtest", "--feed", "cached", root=root)
        self.assertEqual(p.returncode, 2)
        self.assertIn("No bars available", p.stderr)

    def test_synthetic_run_prints_the_warning_banner(self):
        p = run_cli("backtest", "--feed", "synthetic", "--bars", "250",
                    "--conviction", "0.85")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("SYNTHETIC DATA", p.stdout)
        self.assertIn("DO NOT MEASURE STRATEGY PERFORMANCE", p.stdout)

    def test_real_data_run_reports_and_shows_the_benchmark(self):
        p = run_cli("backtest", "--feed", "cached", "--conviction", "0.85")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("QUANTITATIVE PERFORMANCE REPORT", p.stdout)
        self.assertIn("BUY & HOLD BENCHMARK", p.stdout)
        self.assertNotIn("SYNTHETIC DATA", p.stdout)

    def test_benchmark_comparison_is_always_stated(self):
        """A strategy return without its benchmark is misleading, so the CLI
        must always print the comparison verdict."""
        p = run_cli("backtest", "--feed", "cached", "--conviction", "0.85")
        self.assertTrue(
            "BEAT buy & hold" in p.stdout or "LOST TO buy & hold" in p.stdout,
            "expected an explicit benchmark verdict",
        )

    def test_low_conviction_trades_nothing_end_to_end(self):
        p = run_cli("backtest", "--feed", "cached", "--conviction", "0.20",
                    "--threshold", "0.50")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("Total Trades:       0", p.stdout)
        self.assertIn("$100,000.00 -> $100,000.00", p.stdout)

    def test_journal_files_are_written_with_out(self):
        out = Path(tempfile.mkdtemp())
        p = run_cli("backtest", "--feed", "cached", "--conviction", "0.85",
                    "--out", str(out))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue((out / "trades.csv").exists())
        self.assertTrue((out / "daily_pnl.csv").exists())
        self.assertGreater(len((out / "trades.csv").read_text().splitlines()), 1)

    def test_spec_book_against_daily_cache_exits_two(self):
        """The spec book wants 15m/1h/4h bars that the cache does not hold."""
        p = run_cli("backtest", "--feed", "cached", "--book", "spec",
                    "--conviction", "0.85")
        self.assertEqual(p.returncode, 2)

    def test_determinism_across_processes(self):
        a = run_cli("backtest", "--feed", "cached", "--conviction", "0.85").stdout
        b = run_cli("backtest", "--feed", "cached", "--conviction", "0.85").stdout
        self.assertEqual(a, b, "identical inputs must give identical output")
