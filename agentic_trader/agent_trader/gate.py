"""The conviction gate: a fail-closed veto between research and execution.

Most harnesses default a missing or broken confirmation hook to *permit* the
trade, because that keeps the system trading when the research side is down. This
module inverts that. Every path that is not an explicit, fresh, above-threshold
approval returns ``False``:

* no ledger file, unreadable file, malformed JSON  -> deny
* no record for this symbol                        -> deny
* record stale (``now > valid_until``)             -> deny
* record not yet valid (``as_of > now``)           -> deny
* conviction below threshold                       -> deny
* kill switch present                              -> deny
* **any unexpected exception**                     -> deny

The exception clause is the point. A gate that raises on a corrupt ledger and lets
the caller's ``except`` decide is a gate that fails open the first time something
unexpected happens, which is exactly when you most want it closed.

The ledger is written out-of-band by a research process and read here. Reads are
local-disk only: no network call, no LLM call, nothing that can hang the loop.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_trader.config import GateConfig
from agent_trader.types import GateDecision


def _parse_ts(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp, returning None rather than raising."""
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class ConvictionGate:
    """Fail-closed confirmation of entries against a sidecar signal ledger."""

    def __init__(self, config: GateConfig, root: str | Path = ".") -> None:
        self.config = config
        self.root = Path(root)

    # ----------------------------------------------------------------- paths

    @property
    def ledger_path(self) -> Path:
        return self.root / self.config.ledger_path

    @property
    def kill_path(self) -> Path:
        return self.root / self.config.kill_switch_path

    # ---------------------------------------------------------------- ledger

    def read_ledger(self) -> dict[str, Any]:
        """Read the ledger, returning ``{}`` on any problem.

        Never raises: a missing or corrupt ledger must produce denials, not a
        crash that some caller might catch and treat as permission.
        """
        try:
            text = self.ledger_path.read_text(encoding="utf-8")
            data = json.loads(text)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    # ------------------------------------------------------------- decision

    def confirm_entry(
        self,
        symbol: str,
        now: datetime,
        ledger: dict[str, Any] | None = None,
    ) -> GateDecision:
        """Decide whether ``symbol`` may be entered at ``now``."""
        try:
            if not self.config.enabled:
                return GateDecision(True, "gate_disabled")

            if self.kill_path.exists():
                return GateDecision(False, "kill_switch_engaged")

            data = self.read_ledger() if ledger is None else ledger
            signals = data.get("signals")
            if not isinstance(signals, dict):
                return self._no_signal("ledger_missing_signals")

            record = signals.get(symbol)
            if not isinstance(record, dict):
                return self._no_signal("no_signal_for_symbol")

            as_of = _parse_ts(record.get("as_of"))
            valid_until = _parse_ts(record.get("valid_until"))
            if as_of is None or valid_until is None:
                return GateDecision(False, "signal_timestamps_unparseable")

            # Point-in-time integrity: a record stamped in the future would be
            # lookahead smuggled in through the ledger.
            if as_of > now:
                return GateDecision(False, "signal_not_yet_valid")
            if now > valid_until:
                return GateDecision(False, "signal_stale")

            raw = record.get("conviction")
            if not isinstance(raw, (int, float)) or isinstance(raw, bool):
                return GateDecision(False, "conviction_not_numeric")
            conviction = float(raw)
            if conviction != conviction:  # NaN: every comparison is False
                return GateDecision(False, "conviction_nan")

            conviction = self._apply_event_risk(conviction, record)

            if conviction < self.config.veto_threshold:
                return GateDecision(
                    False,
                    f"conviction_below_threshold_{conviction:.3f}<{self.config.veto_threshold:.3f}",
                    conviction,
                )
            return GateDecision(True, "conviction_confirmed", conviction)

        except Exception as exc:  # noqa: BLE001 - deliberate catch-all
            # The whole reason this class exists.
            return GateDecision(False, f"gate_exception_fail_closed:{type(exc).__name__}")

    def _no_signal(self, reason: str) -> GateDecision:
        if self.config.allow_when_no_signal:
            return GateDecision(True, f"{reason}_but_allowed_by_config")
        return GateDecision(False, reason)

    def _apply_event_risk(self, conviction: float, record: dict[str, Any]) -> float:
        """Dampen conviction when a prediction market prices an adverse event.

        With an implied probability ``p`` above the threshold, conviction is capped
        at ``1 - p``: if the market says there is a 70% chance of the adverse event,
        confidence in the trade cannot exceed 30%.
        """
        p = record.get("event_risk_probability")
        if not isinstance(p, (int, float)) or isinstance(p, bool):
            return conviction
        p = float(p)
        if not 0.0 <= p <= 1.0:
            return conviction
        if p <= self.config.event_risk_threshold:
            return conviction
        return min(conviction, 1.0 - p)


def write_ledger(
    path: str | Path,
    signals: dict[str, dict[str, Any]],
    *,
    generated_at: datetime | None = None,
) -> None:
    """Atomically write a signal ledger.

    Atomic because the execution side may read at any moment: a half-written
    ledger must never be observable. ``os.replace`` on the same filesystem is the
    rename-into-place primitive that guarantees it.
    """
    import os
    import tempfile

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": (generated_at or datetime.now()).isoformat(),
        "signals": signals,
    }
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
        os.chmod(tmp, 0o644)  # mkstemp defaults to 0600; data files are not secrets
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
