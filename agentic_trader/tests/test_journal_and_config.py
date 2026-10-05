"""CSV journal output, config round-tripping, and feed contracts."""

import csv
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from agent_trader.config import AppConfig, GateConfig, RiskConfig, default_strategies
from agent_trader.feeds import CachedFeed, FeedError, LLMQuantFeed, SyntheticFeed
from agent_trader.feeds.cached import bars_from_payload, cache_filename, write_cache
from agent_trader.journal import write_daily_pnl, write_trades
from agent_trader.types import EquityPoint, Side, Trade
from tests.helpers import T0


class TestConfig(unittest.TestCase):
    def test_json_round_trip(self):
        c = AppConfig(strategies=default_strategies())
        self.assertEqual(AppConfig.from_json(c.to_json()), c)

    def test_load_from_file(self):
        c = AppConfig(strategies=default_strategies())
        p = Path(tempfile.mkdtemp()) / "cfg.json"
        p.write_text(c.to_json())
        self.assertEqual(AppConfig.load(p), c)

    def test_unknown_key_rejected(self):
        with self.assertRaises(ValueError):
            AppConfig.from_dict({"nope": 1})

    def test_invalid_risk_rejected(self):
        for kwargs in [{"risk_per_trade": 0}, {"risk_per_trade": 1.0},
                       {"max_portfolio_drawdown": 0}, {"starting_equity": -1}]:
            with self.assertRaises(ValueError):
                RiskConfig(**kwargs)

    def test_invalid_threshold_rejected(self):
        with self.assertRaises(ValueError):
            GateConfig(veto_threshold=1.5)

    def test_default_book_covers_five_symbols(self):
        symbols = {s.symbol for s in default_strategies()}
        self.assertEqual(symbols, {"SPY", "QQQ", "BTC-USD", "GLD", "USO"})

    def test_qqq_band_is_wider_than_spy(self):
        by = {s.symbol: s for s in default_strategies()}
        self.assertGreater(by["QQQ"].band_sigma, by["SPY"].band_sigma)


class TestJournal(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.trades = [
            Trade("SPY", Side.LONG, 10.0, 100.0, 110.0, T0,
                  T0 + timedelta(hours=1), 98.5, 1.5, "target", "mean_reversion"),
            Trade("GLD", Side.SHORT, 5.0, 200.0, 210.0, T0,
                  T0 + timedelta(days=1), -51.0, 1.0, "stop_hit", "trend_following"),
        ]

    def test_trades_csv_has_header_and_rows(self):
        p = write_trades(self.dir / "trades.csv", self.trades)
        with p.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["symbol"], "SPY")
        self.assertEqual(rows[1]["exit_reason"], "stop_hit")
        self.assertEqual(rows[0]["strategy"], "mean_reversion")

    def test_empty_trades_still_writes_header(self):
        p = write_trades(self.dir / "empty.csv", [])
        with p.open(newline="", encoding="utf-8") as fh:
            self.assertEqual(list(csv.DictReader(fh)), [])
        self.assertIn("symbol", p.read_text().splitlines()[0])

    def test_daily_pnl_groups_by_calendar_day(self):
        curve = [
            EquityPoint(T0, 100_000.0),
            EquityPoint(T0 + timedelta(hours=1), 101_000.0),
            EquityPoint(T0 + timedelta(days=1), 99_000.0),
        ]
        p = write_daily_pnl(self.dir / "daily.csv", curve)
        with p.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["equity_open"], "100000.00")
        self.assertEqual(rows[0]["equity_close"], "101000.00")
        self.assertEqual(rows[0]["bars"], "2")

    def test_writes_leave_no_temp_files(self):
        write_trades(self.dir / "t.csv", self.trades)
        write_daily_pnl(self.dir / "d.csv", [EquityPoint(T0, 1.0)])
        self.assertEqual(list(self.dir.glob("*.tmp")), [])


class TestSyntheticFeed(unittest.TestCase):
    def test_deterministic_for_same_seed(self):
        f = SyntheticFeed(seed=7, n_bars=50)
        self.assertEqual([b.close for b in f.load("SPY", "1h")],
                         [b.close for b in f.load("SPY", "1h")])

    def test_different_symbols_differ(self):
        f = SyntheticFeed(seed=7, n_bars=50)
        self.assertNotEqual([b.close for b in f.load("SPY", "1h")],
                            [b.close for b in f.load("QQQ", "1h")])

    def test_bars_are_sorted_and_valid(self):
        bars = SyntheticFeed(seed=3, n_bars=200).load("T", "15m")
        self.assertTrue(all(bars[i].ts < bars[i + 1].ts for i in range(len(bars) - 1)))
        for b in bars:
            self.assertLessEqual(b.low, b.open)
            self.assertLessEqual(b.open, b.high)
            self.assertLessEqual(b.low, b.close)
            self.assertLessEqual(b.close, b.high)

    def test_unknown_interval_rejected(self):
        with self.assertRaises(ValueError):
            SyntheticFeed().load("T", "7s")

    def test_flagged_as_synthetic(self):
        self.assertTrue(SyntheticFeed().is_synthetic)
        self.assertFalse(CachedFeed().is_synthetic)


class TestCachedFeed(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def test_filename_is_filesystem_safe(self):
        self.assertEqual(cache_filename("BTC-USD", "1h"), "BTC-USD_1h.json")
        self.assertEqual(cache_filename("BTC/USD", "1h"), "BTC-USD_1h.json")

    def test_round_trip(self):
        rows = [{"ts": (T0 + timedelta(hours=i)).isoformat(), "open": 10.0,
                 "high": 11.0, "low": 9.0, "close": 10.5, "volume": 100.0}
                for i in range(3)]
        write_cache(self.dir, "SPY", "1h", rows, "test")
        bars = CachedFeed(self.dir).load("SPY", "1h")
        self.assertEqual(len(bars), 3)
        self.assertEqual(bars[0].symbol, "SPY")

    def test_missing_cache_raises_actionable_error(self):
        with self.assertRaises(FeedError) as ctx:
            CachedFeed(self.dir).load("NOPE", "1h")
        self.assertIn("docs/DATA.md", str(ctx.exception))

    def test_malformed_bar_raises_rather_than_dropping(self):
        with self.assertRaises(FeedError):
            bars_from_payload({"bars": [{"ts": T0.isoformat(), "open": 1.0}]}, "X")

    def test_bad_timestamp_raises(self):
        with self.assertRaises(FeedError):
            bars_from_payload({"bars": [{"ts": "nope", "open": 1.0, "high": 1.0,
                                         "low": 1.0, "close": 1.0, "volume": 1.0}]}, "X")

    def test_duplicate_timestamp_raises(self):
        row = {"ts": T0.isoformat(), "open": 1.0, "high": 1.0, "low": 1.0,
               "close": 1.0, "volume": 1.0}
        with self.assertRaises(FeedError):
            bars_from_payload({"bars": [row, dict(row)]}, "X")

    def test_bars_not_a_list_raises(self):
        with self.assertRaises(FeedError):
            bars_from_payload({"bars": "nope"}, "X")

    def test_unsorted_input_is_sorted(self):
        rows = [{"ts": (T0 + timedelta(hours=i)).isoformat(), "open": 10.0,
                 "high": 11.0, "low": 9.0, "close": 10.5, "volume": 1.0}
                for i in (2, 0, 1)]
        bars = bars_from_payload({"bars": rows}, "X")
        self.assertTrue(all(bars[i].ts < bars[i + 1].ts for i in range(2)))

    def test_available_reports_metadata(self):
        rows = [{"ts": T0.isoformat(), "open": 1.0, "high": 1.0, "low": 1.0,
                 "close": 1.0, "volume": 1.0}]
        write_cache(self.dir, "SPY", "1h", rows, "unit-test")
        got = CachedFeed(self.dir).available()
        self.assertEqual(got[0][0], "SPY")
        self.assertEqual(got[0][2], 1)

    def test_write_validates_before_committing(self):
        with self.assertRaises(FeedError):
            write_cache(self.dir, "BAD", "1h", [{"ts": "x"}], "test")
        self.assertFalse((self.dir / "BAD_1h.json").exists(),
                         "invalid payload must not leave a file behind")


class TestLLMQuantFeed(unittest.TestCase):
    def test_unconfigured_feed_refuses_with_guidance(self):
        f = LLMQuantFeed(api_key=None)
        self.assertFalse(f.configured)
        with self.assertRaises(FeedError) as ctx:
            f.load("SPY", "1h")
        self.assertIn("LLMQUANT_API_KEY", str(ctx.exception))

    def test_parses_aliased_fields_via_fake_transport(self):
        def fake(url, headers):
            self.assertTrue(url.startswith("https://"))
            self.assertIn("Bearer", headers["Authorization"])
            return json.dumps({"data": [
                {"timestamp": T0.isoformat(), "o": 10, "h": 11, "l": 9,
                 "c": 10.5, "v": 100}]})

        bars = LLMQuantFeed(api_key="k", transport=fake).load("SPY", "1h")
        self.assertEqual(bars[0].close, 10.5)
        self.assertEqual(bars[0].volume, 100.0)

    def test_missing_volume_defaults_to_zero(self):
        def fake(url, headers):
            return json.dumps({"data": [{"ts": T0.isoformat(), "open": 1, "high": 2,
                                         "low": 0.5, "close": 1.5}]})

        self.assertEqual(
            LLMQuantFeed(api_key="k", transport=fake).load("S", "1h")[0].volume, 0.0)

    def test_unrecognisable_response_raises(self):
        def fake(url, headers):
            return json.dumps({"unexpected": True})

        with self.assertRaises(FeedError):
            LLMQuantFeed(api_key="k", transport=fake).load("S", "1h")

    def test_non_json_response_raises(self):
        with self.assertRaises(FeedError):
            LLMQuantFeed(api_key="k",
                         transport=lambda u, h: "<html>502</html>").load("S", "1h")
