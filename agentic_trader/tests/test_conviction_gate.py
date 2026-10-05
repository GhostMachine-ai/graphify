"""The fail-closed conviction gate.

Each test names a way the gate could fail *open*. Everything that is not an
explicit, fresh, above-threshold approval must deny.
"""

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from agent_trader.config import GateConfig
from agent_trader.gate import ConvictionGate, write_ledger

NOW = datetime(2026, 6, 1, 12, 0, 0)


def rec(conviction, **over):
    d = {
        "as_of": (NOW - timedelta(minutes=5)).isoformat(),
        "valid_until": (NOW + timedelta(minutes=30)).isoformat(),
        "conviction": conviction,
    }
    d.update(over)
    return d


def led(record):
    return {"signals": {"SPY": record}}


class TestGate(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.gate = ConvictionGate(GateConfig(veto_threshold=0.50), root=self.root)

    def allowed(self, ledger):
        return self.gate.confirm_entry("SPY", NOW, ledger=ledger).allowed

    # ---------------------------------------------------------------- permits

    def test_high_conviction_allows(self):
        self.assertTrue(self.allowed(led(rec(0.85))))

    def test_exactly_at_threshold_allows(self):
        self.assertTrue(self.allowed(led(rec(0.50))))

    def test_disabled_gate_allows(self):
        g = ConvictionGate(GateConfig(enabled=False), root=self.root)
        self.assertTrue(g.confirm_entry("SPY", NOW, ledger={}).allowed)

    # ----------------------------------------------------------------- denies

    def test_low_conviction_denies(self):
        self.assertFalse(self.allowed(led(rec(0.20))))

    def test_just_below_threshold_denies(self):
        self.assertFalse(self.allowed(led(rec(0.4999))))

    def test_missing_symbol_denies(self):
        self.assertFalse(self.allowed({"signals": {"QQQ": rec(0.99)}}))

    def test_empty_ledger_denies(self):
        self.assertFalse(self.allowed({}))

    def test_stale_signal_denies(self):
        stale = rec(0.99, valid_until=(NOW - timedelta(minutes=1)).isoformat())
        self.assertFalse(self.allowed(led(stale)))

    def test_future_as_of_denies_point_in_time(self):
        future = rec(0.99, as_of=(NOW + timedelta(minutes=10)).isoformat())
        d = self.gate.confirm_entry("SPY", NOW, ledger=led(future))
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason, "signal_not_yet_valid")

    def test_unparseable_timestamps_deny(self):
        bad = {"as_of": "whenever", "valid_until": "later", "conviction": 0.99}
        self.assertFalse(self.allowed(led(bad)))

    def test_string_conviction_denies(self):
        self.assertFalse(self.allowed(led(rec("0.99"))))

    def test_bool_conviction_denies(self):
        # True would pass a naive `>= threshold` check, since bool is an int.
        self.assertFalse(self.allowed(led(rec(True))))

    def test_nan_conviction_denies(self):
        self.assertFalse(self.allowed(led(rec(float("nan")))))

    def test_signals_not_a_dict_denies(self):
        self.assertFalse(self.allowed({"signals": [1, 2, 3]}))

    def test_record_not_a_dict_denies(self):
        self.assertFalse(self.allowed({"signals": {"SPY": "yes"}}))

    def test_missing_ledger_file_denies(self):
        self.assertFalse(self.gate.confirm_entry("SPY", NOW).allowed)

    def test_corrupt_ledger_file_denies(self):
        p = self.root / "shared" / "signals" / "conviction.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{ not json")
        self.assertFalse(self.gate.confirm_entry("SPY", NOW).allowed)

    def test_exception_inside_gate_fails_closed(self):
        class Exploding(dict):
            def get(self, *a, **k):
                raise RuntimeError("boom")

        d = self.gate.confirm_entry("SPY", NOW, ledger=Exploding())
        self.assertFalse(d.allowed)
        self.assertIn("fail_closed", d.reason)

    def test_kill_switch_overrides_perfect_signal(self):
        (self.root / "shared" / "control").mkdir(parents=True, exist_ok=True)
        (self.root / "shared" / "control" / "KILL").touch()
        d = self.gate.confirm_entry("SPY", NOW, ledger=led(rec(0.99)))
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason, "kill_switch_engaged")

    # ------------------------------------------------------------ event risk

    def test_event_risk_above_threshold_dampens_conviction(self):
        d = self.gate.confirm_entry(
            "SPY", NOW, ledger=led(rec(0.85, event_risk_probability=0.80))
        )
        self.assertFalse(d.allowed)
        self.assertAlmostEqual(d.conviction, 0.20, places=9)

    def test_event_risk_below_threshold_is_ignored(self):
        self.assertTrue(self.allowed(led(rec(0.85, event_risk_probability=0.60))))

    def test_out_of_range_event_risk_ignored(self):
        self.assertTrue(self.allowed(led(rec(0.85, event_risk_probability=1.7))))

    def test_non_numeric_event_risk_ignored(self):
        self.assertTrue(self.allowed(led(rec(0.85, event_risk_probability="high"))))

    # ----------------------------------------------------- allow_when_no_signal

    def test_opt_in_allows_missing_signal(self):
        g = ConvictionGate(
            GateConfig(allow_when_no_signal=True), root=self.root
        )
        self.assertTrue(g.confirm_entry("SPY", NOW, ledger={"signals": {}}).allowed)

    def test_opt_in_still_denies_a_low_conviction_signal(self):
        g = ConvictionGate(
            GateConfig(allow_when_no_signal=True, veto_threshold=0.5), root=self.root
        )
        self.assertFalse(g.confirm_entry("SPY", NOW, ledger=led(rec(0.1))).allowed)

    def test_default_is_fail_closed(self):
        self.assertFalse(GateConfig().allow_when_no_signal)


class TestLedgerWriting(unittest.TestCase):
    def test_round_trip_through_disk(self):
        root = Path(tempfile.mkdtemp())
        path = root / "shared" / "signals" / "conviction.json"
        write_ledger(path, {"SPY": rec(0.9)})
        gate = ConvictionGate(GateConfig(), root=root)
        self.assertTrue(gate.confirm_entry("SPY", NOW).allowed)

    def test_write_is_atomic_and_leaves_no_temp_files(self):
        root = Path(tempfile.mkdtemp())
        path = root / "shared" / "signals" / "conviction.json"
        write_ledger(path, {"SPY": rec(0.9)})
        self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_written_payload_has_generated_at(self):
        root = Path(tempfile.mkdtemp())
        path = root / "led.json"
        write_ledger(path, {"SPY": rec(0.9)})
        self.assertIn("generated_at", json.loads(path.read_text()))
