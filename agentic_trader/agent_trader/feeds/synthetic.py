"""Seeded synthetic bars.

**This is a test fixture, not a research tool.** Read this before using it for
anything that produces a performance number.

The generator is a geometric Brownian motion: increments are independent and
identically distributed. It therefore contains, by construction:

* no trend persistence for a momentum rule to capture,
* no mean reversion for a Bollinger rule to capture,
* no relationship between volume and price for a volume filter to confirm.

Backtesting a directional strategy on GBM measures nothing about the strategy. Any
profit or loss is sampling noise, and reporting it as a result -- in either
direction -- is misleading. What this *is* good for is determinism: the same seed
gives the same bars, so engine mechanics (fills, stops, gate denials, accounting
identities) can be asserted exactly.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta

from agent_trader.types import Bar

INTERVAL_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400,
}


class SyntheticFeed:
    """Deterministic GBM bars. Fixture only -- see the module docstring."""

    #: Flag read by the CLI so synthetic results are labelled, never reported bare.
    is_synthetic = True

    def __init__(
        self,
        seed: int = 42,
        n_bars: int = 500,
        start_price: float = 100.0,
        annual_vol: float = 0.25,
        drift: float = 0.0,
        start: datetime | None = None,
    ) -> None:
        self.seed = seed
        self.n_bars = n_bars
        self.start_price = start_price
        self.annual_vol = annual_vol
        self.drift = drift
        self.start = start or datetime(2026, 1, 1)

    def load(self, symbol: str, interval: str) -> list[Bar]:
        step = INTERVAL_SECONDS.get(interval)
        if step is None:
            raise ValueError(f"unknown interval {interval!r}")
        # Seed per (symbol, interval) so two symbols are not identical series but
        # each remains reproducible across runs.
        rng = random.Random(f"{self.seed}:{symbol}:{interval}")
        periods_per_year = (365.25 * 24 * 3600) / step
        sigma = self.annual_vol / math.sqrt(periods_per_year)
        mu = self.drift / periods_per_year

        bars: list[Bar] = []
        price = self.start_price
        for i in range(self.n_bars):
            shock = rng.gauss(mu, sigma)
            close = max(0.01, price * math.exp(shock))
            hi_wick = abs(rng.gauss(0, sigma)) * price
            lo_wick = abs(rng.gauss(0, sigma)) * price
            high = max(price, close) + hi_wick
            low = max(0.005, min(price, close) - lo_wick)
            volume = max(1.0, rng.lognormvariate(11.0, 0.4))
            bars.append(Bar(
                symbol=symbol,
                ts=self.start + timedelta(seconds=step * i),
                open=price, high=high, low=low, close=close, volume=volume,
            ))
            price = close
        return bars
