"""Buy-and-hold benchmark.

A strategy return reported on its own is close to meaningless. The real-data run
in this repository returned **-5.89%** over a period in which equal-weight
buy-and-hold on the same five instruments returned **+60.75%** -- a shortfall of
66 percentage points. A report showing only "-5.89%" understates that badly, and
one showing only "profit factor 0.84" hides it completely.

So the benchmark is computed by the same code path that prints the performance
report, and the CLI always shows both.

Deliberately generous to the benchmark: no fees, no slippage, no rebalancing.
That is the right asymmetry. The benchmark is the thing a strategy has to beat to
justify its own existence, so it should be given the benefit of the doubt.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from agent_trader.types import Bar


@dataclass(frozen=True)
class BenchmarkResult:
    """Equal-weight buy-and-hold over the same instruments and window."""

    starting_equity: float
    ending_equity: float
    per_symbol: dict[str, float]

    @property
    def total_return(self) -> float:
        if self.starting_equity <= 0:
            return 0.0
        return self.ending_equity / self.starting_equity - 1

    def render(self, strategy_return: float | None = None) -> str:
        lines = [
            "-" * 54,
            " BUY & HOLD BENCHMARK (equal weight, no costs)",
            "-" * 54,
        ]
        for sym, r in sorted(self.per_symbol.items()):
            lines.append(f"  {sym:<10}{r:+9.2%}")
        lines.append(f"  {'TOTAL':<10}{self.total_return:+9.2%}  "
                     f"(${self.ending_equity:,.2f})")
        if strategy_return is not None:
            diff = strategy_return - self.total_return
            verdict = "BEAT" if diff > 0 else "LOST TO"
            lines += [
                "-" * 54,
                f"  Strategy {strategy_return:+.2%} vs benchmark "
                f"{self.total_return:+.2%}",
                f"  Strategy {verdict} buy & hold by {abs(diff):.2%}",
            ]
        return "\n".join(lines)


def equal_weight_buy_and_hold(
    bars_by_symbol: dict[str, Sequence[Bar]], starting_equity: float
) -> BenchmarkResult:
    """Split equity evenly across symbols, buy at the first close, hold to the last."""
    usable = {s: b for s, b in bars_by_symbol.items() if b}
    if not usable:
        return BenchmarkResult(starting_equity, starting_equity, {})
    per = starting_equity / len(usable)
    ending, per_symbol = 0.0, {}
    for sym, bars in usable.items():
        first, last = bars[0].close, bars[-1].close
        r = (last / first - 1) if first > 0 else 0.0
        per_symbol[sym] = r
        ending += per * (1 + r)
    return BenchmarkResult(starting_equity, ending, per_symbol)
