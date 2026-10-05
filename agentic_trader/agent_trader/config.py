"""Configuration objects, JSON-serializable so a run is reproducible from a file."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any


@dataclass
class RiskConfig:
    """Risk limits.

    ``risk_per_trade`` is a *fraction of equity risked if price moves one ATR
    against the position*, not a fraction of equity deployed. That is the whole
    point of ATR sizing: a 1% setting loses ~1% on a 1-ATR adverse move whether
    the instrument is GLD or BTC.
    """

    starting_equity: float = 100_000.0
    risk_per_trade: float = 0.01
    max_portfolio_drawdown: float = 0.10
    max_concurrent_positions: int = 5
    #: Cap on one position's notional as a fraction of equity. With
    #: max_concurrent_positions=5, 0.20 means a fully-invested book is exactly 100%
    #: of equity -- no implied leverage. This cap frequently binds before the ATR
    #: risk target does; see RiskManager.size_detail.
    max_notional_per_position: float = 0.20
    correlation_filter_enabled: bool = True

    def __post_init__(self) -> None:
        if not 0 < self.risk_per_trade < 1:
            raise ValueError("risk_per_trade must be in (0, 1)")
        if not 0 < self.max_portfolio_drawdown < 1:
            raise ValueError("max_portfolio_drawdown must be in (0, 1)")
        if self.starting_equity <= 0:
            raise ValueError("starting_equity must be positive")


@dataclass
class StrategyConfig:
    """Per-symbol strategy parameters."""

    name: str
    symbol: str
    interval: str
    #: Bollinger band width in standard deviations (mean reversion only).
    band_sigma: float = 2.0
    sma_period: int = 20
    donchian_period: int = 20
    volume_surge_multiple: float = 1.5
    fast_ema: int = 50
    slow_ema: int = 200
    atr_period: int = 14
    atr_stop_multiple: float = 2.0


@dataclass
class GateConfig:
    """Conviction-gate behaviour.

    ``allow_when_no_signal`` defaults to ``False``. This is deliberate and is the
    single most important default in the package: absence of a conviction signal
    denies entry rather than permitting it.
    """

    enabled: bool = True
    veto_threshold: float = 0.50
    allow_when_no_signal: bool = False
    ledger_path: str = "shared/signals/conviction.json"
    kill_switch_path: str = "shared/control/KILL"
    #: Prediction-market probability above which conviction is dampened.
    event_risk_threshold: float = 0.65

    def __post_init__(self) -> None:
        if not 0 <= self.veto_threshold <= 1:
            raise ValueError("veto_threshold must be in [0, 1]")


@dataclass
class ExecutionConfig:
    """Fill-simulation assumptions. Optimistic values here flatter a backtest, so
    the defaults are intentionally non-zero."""

    slippage_bps: float = 2.0
    fee_bps: float = 1.0
    #: Fills happen at the next bar's open, never the signal bar's close.
    fill_at_next_open: bool = True


@dataclass
class AppConfig:
    """Top-level run configuration."""

    risk: RiskConfig = field(default_factory=RiskConfig)
    gate: GateConfig = field(default_factory=GateConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    strategies: list[StrategyConfig] = field(default_factory=list)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(asdict(self), indent=indent, sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppConfig:
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown config keys: {sorted(unknown)}")
        return cls(
            risk=RiskConfig(**data.get("risk", {})),
            gate=GateConfig(**data.get("gate", {})),
            execution=ExecutionConfig(**data.get("execution", {})),
            strategies=[StrategyConfig(**s) for s in data.get("strategies", [])],
        )

    @classmethod
    def from_json(cls, text: str) -> AppConfig:
        return cls.from_dict(json.loads(text))

    @classmethod
    def load(cls, path: str | Path) -> AppConfig:
        return cls.from_json(Path(path).read_text(encoding="utf-8"))


def default_strategies() -> list[StrategyConfig]:
    """The multi-asset book described in the spec."""
    return [
        StrategyConfig("mean_reversion", "SPY", "15m", band_sigma=1.5, atr_stop_multiple=2.0),
        StrategyConfig("mean_reversion", "QQQ", "15m", band_sigma=1.8, atr_stop_multiple=2.0),
        StrategyConfig("momentum_breakout", "BTC-USD", "1h", atr_stop_multiple=2.0),
        StrategyConfig("trend_following", "GLD", "4h", atr_stop_multiple=3.0),
        StrategyConfig("trend_following", "USO", "4h", atr_stop_multiple=3.0),
    ]


def daily_book() -> list[StrategyConfig]:
    """The book as actually runnable against the bars in ``data/cache``.

    This differs from :func:`default_strategies` and the differences are
    substitutions forced by the data source, not preferences. They are listed here
    rather than in a commit message because anyone reading a result needs them:

    * **SPY / QQQ mean reversion on 1d, not 15m.** The RobinHood historicals API
      offers no ``15minute`` interval at all (15second/30second/minute/5minute/
      10minute/30minute/hour/4hour), so a true 15m series would have to be
      aggregated from 5-minute bars -- roughly six times the data for no gain at
      this sample size. Band widths are preserved: 1.5 sigma for SPY, 1.8 for QQQ.

    * **GLD / USO trend following on 1d, not 4h.** Under regular trading hours a
      4-hour bar for these ETFs *is* approximately one bar per session: a 3-month
      request returned 66 bars. Daily is therefore the honest label for the same
      series, and EMA50/EMA200 on daily bars is the canonical golden cross anyway.

    * **IBIT instead of BTC-USD, on 1d not 1h.** The RobinHood MCP surface exposes
      crypto only as real-time quotes (``get_crypto_quotes``); there is no crypto
      historicals tool, so BTC spot history is unavailable here. IBIT is a spot
      bitcoin ETF and a reasonable **proxy**, but it is not BTC: it trades only in
      US market hours, so it gaps across the nights and weekends when bitcoin keeps
      moving, and it carries fund fees and tracking error. Treat it as correlated
      exposure, not as bitcoin.

    Period lengths are unchanged from the spec, which matters for interpretation:
    an ATR14 on daily bars measures two weeks of volatility, where on 15m bars it
    measured three and a half hours.
    """
    return [
        StrategyConfig("mean_reversion", "SPY", "1d", band_sigma=1.5,
                       atr_stop_multiple=2.0),
        StrategyConfig("mean_reversion", "QQQ", "1d", band_sigma=1.8,
                       atr_stop_multiple=2.0),
        StrategyConfig("momentum_breakout", "IBIT", "1d", atr_stop_multiple=2.0),
        StrategyConfig("trend_following", "GLD", "1d", atr_stop_multiple=3.0),
        StrategyConfig("trend_following", "USO", "1d", atr_stop_multiple=3.0),
    ]
