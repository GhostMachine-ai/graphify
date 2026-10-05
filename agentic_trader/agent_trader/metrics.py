"""Performance metrics.

Annualisation requires knowing the sampling frequency of the equity curve, so
``periods_per_year`` is an explicit argument with no silent default of 252: a
15-minute equity curve annualised as if it were daily overstates Sharpe by about
an order of magnitude, which is a very easy way to report a fantasy.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from agent_trader.types import EquityPoint, Trade


@dataclass(frozen=True)
class Performance:
    """Summary statistics for a run."""

    total_trades: int
    wins: int
    losses: int
    win_rate: float
    total_pnl: float
    total_return: float
    profit_factor: float
    sharpe: float
    sortino: float
    max_drawdown: float
    avg_win: float
    avg_loss: float
    total_fees: float
    starting_equity: float
    ending_equity: float
    periods_per_year: int

    def render(self) -> str:
        """Human-readable report."""
        pf = "inf" if self.profit_factor == float("inf") else f"{self.profit_factor:.2f}"
        return "\n".join([
            "=" * 54,
            "         QUANTITATIVE PERFORMANCE REPORT",
            "=" * 54,
            f" Total Trades:       {self.total_trades}",
            f" Win Rate:           {self.win_rate:.2%} ({self.wins}W / {self.losses}L)",
            f" Total PnL:          ${self.total_pnl:,.2f}",
            f" Total Return:       {self.total_return:.2%}",
            f" Profit Factor:      {pf}",
            f" Sharpe Ratio:       {self.sharpe:.3f}",
            f" Sortino Ratio:      {self.sortino:.3f}",
            f" Max Drawdown:       {self.max_drawdown:.2%}",
            f" Avg Win / Avg Loss: ${self.avg_win:,.2f} / ${self.avg_loss:,.2f}",
            f" Total Fees:         ${self.total_fees:,.2f}",
            f" Equity:             ${self.starting_equity:,.2f} -> ${self.ending_equity:,.2f}",
            "=" * 54,
            f" (annualised at {self.periods_per_year} periods/year)",
        ])


def returns_from_equity(curve: Sequence[EquityPoint]) -> list[float]:
    """Simple period-over-period returns. Non-positive equity ends the series."""
    out: list[float] = []
    for prev, cur in zip(curve, curve[1:]):
        if prev.equity <= 0:
            break
        out.append((cur.equity - prev.equity) / prev.equity)
    return out


def max_drawdown(curve: Sequence[EquityPoint]) -> float:
    """Largest peak-to-trough fractional decline in the equity curve."""
    peak, worst = float("-inf"), 0.0
    for pt in curve:
        peak = max(peak, pt.equity)
        if peak > 0:
            worst = max(worst, (peak - pt.equity) / peak)
    return worst


def _degenerate_spread(sd: float, mean: float) -> bool:
    """True when ``sd`` is numerically indistinguishable from zero.

    An exact ``sd == 0`` test is not enough. For a constant series such as
    ``[0.01] * 10`` the computed mean is 0.009999999999999998 rather than 0.01, so
    the deviations are ~1e-18 and the variance is a denormal -- non-zero, but pure
    floating-point noise. Dividing by it yields a Sharpe of ~8.7e16, which would be
    printed in a report as though it meant something. Compare the spread against the
    magnitude of the mean instead.
    """
    if not math.isfinite(sd) or not math.isfinite(mean):
        return True
    return sd <= max(abs(mean), 1.0) * 1e-12


def sharpe(returns: Sequence[float], periods_per_year: int) -> float:
    """Annualised Sharpe at a zero risk-free rate. Zero when undefined."""
    n = len(returns)
    if n < 2:
        return 0.0
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    sd = math.sqrt(var)
    if _degenerate_spread(sd, mean):
        return 0.0
    return (mean / sd) * math.sqrt(periods_per_year)


def sortino(returns: Sequence[float], periods_per_year: int) -> float:
    """Annualised Sortino: downside deviation only.

    Returns 0.0 when there is no downside at all, rather than infinity -- an
    infinite ratio from a handful of periods is noise, not skill.
    """
    n = len(returns)
    if n < 2:
        return 0.0
    mean = sum(returns) / n
    downside = [r for r in returns if r < 0]
    if not downside:
        return 0.0
    dd = math.sqrt(sum(r * r for r in downside) / len(downside))
    if _degenerate_spread(dd, mean):
        return 0.0
    return (mean / dd) * math.sqrt(periods_per_year)


def summarize(
    trades: Sequence[Trade],
    curve: Sequence[EquityPoint],
    starting_equity: float,
    periods_per_year: int,
) -> Performance:
    """Build a :class:`Performance` from trades and an equity curve."""
    wins = [t for t in trades if t.is_win]
    losses = [t for t in trades if not t.is_win]
    gross_win = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    total_pnl = sum(t.pnl for t in trades)
    ending = curve[-1].equity if curve else starting_equity
    rets = returns_from_equity(curve)

    if gross_loss == 0:
        pf = float("inf") if gross_win > 0 else 0.0
    else:
        pf = gross_win / gross_loss

    return Performance(
        total_trades=len(trades),
        wins=len(wins),
        losses=len(losses),
        win_rate=(len(wins) / len(trades)) if trades else 0.0,
        total_pnl=total_pnl,
        total_return=(ending - starting_equity) / starting_equity if starting_equity else 0.0,
        profit_factor=pf,
        sharpe=sharpe(rets, periods_per_year),
        sortino=sortino(rets, periods_per_year),
        max_drawdown=max_drawdown(curve),
        avg_win=(gross_win / len(wins)) if wins else 0.0,
        avg_loss=(gross_loss / len(losses)) if losses else 0.0,
        total_fees=sum(t.fees for t in trades),
        starting_equity=starting_equity,
        ending_equity=ending,
        periods_per_year=periods_per_year,
    )
