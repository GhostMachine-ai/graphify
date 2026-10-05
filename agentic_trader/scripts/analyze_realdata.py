#!/usr/bin/env python3
"""Real-data analysis: the book, the gate proof, and a buy-and-hold benchmark."""
from __future__ import annotations
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_trader.backtest import Backtester
from agent_trader.config import AppConfig, GateConfig, RiskConfig, daily_book
from agent_trader.feeds import CachedFeed

ROOT = Path(__file__).resolve().parent.parent
BOOK = daily_book()
feed = CachedFeed(ROOT / "data" / "cache")
bars = {s.symbol: feed.load(s.symbol, s.interval) for s in BOOK}


def run(conviction, max_dd=0.10, threshold=0.50):
    cfg = AppConfig(risk=RiskConfig(max_portfolio_drawdown=max_dd),
                    gate=GateConfig(veto_threshold=threshold), strategies=BOOK)
    bt = Backtester(cfg, root=ROOT)
    first = min(b[0].ts for b in bars.values())
    last = max(b[-1].ts for b in bars.values())
    led = {"signals": {s: {"as_of": (first - timedelta(days=1)).isoformat(),
                           "valid_until": (last + timedelta(days=1)).isoformat(),
                           "conviction": conviction} for s in bars}}
    return bt, *bt.run(bars, ledger=led)


print("=" * 72)
print("1. GATE PROOF ON REAL DATA")
print("=" * 72)
_, lo, plo = run(0.20)
_, hi, phi = run(0.85)
print(f"  conviction 0.20 (< 0.50 threshold): {len(lo.trades):3d} trades, "
      f"equity ${lo.ending_equity:,.2f}")
print(f"  conviction 0.85 (> 0.50 threshold): {len(hi.trades):3d} trades, "
      f"equity ${hi.ending_equity:,.2f}")
assert len(lo.trades) == 0 and lo.ending_equity == 100_000.0
print("  -> veto is exact: identical data, zero trades, equity untouched.")

print()
print("=" * 72)
print("2. BUY-AND-HOLD BENCHMARK (equal weight, 20% each, no costs)")
print("=" * 72)
start_eq = 100_000.0
per = start_eq / len(bars)
bh_total = 0.0
for sym in sorted(bars):
    b = bars[sym]
    r = b[-1].close / b[0].close - 1
    bh_total += per * (1 + r)
    print(f"  {sym:6s} {b[0].close:9.2f} -> {b[-1].close:9.2f}   {r:+7.1%}")
bh_ret = bh_total / start_eq - 1
print(f"  {'TOTAL':6s} equal-weight buy & hold: {bh_ret:+.2%}  (${bh_total:,.2f})")

print()
print("=" * 72)
print("3. STRATEGY RESULT vs BENCHMARK")
print("=" * 72)
_, full, pfull = run(0.85, max_dd=0.95)   # breaker effectively off
for label, res, perf in [("with 10% breaker", hi, phi),
                         ("breaker off (95%)", full, pfull)]:
    print(f"  {label:20s} return {perf.total_return:+7.2%}  "
          f"trades {perf.total_trades:3d}  win {perf.win_rate:6.2%}  "
          f"PF {perf.profit_factor:5.2f}  Sharpe {perf.sharpe:+6.2f}  "
          f"maxDD {perf.max_drawdown:6.2%}  halted={res.halted}")
print(f"  {'buy & hold':20s} return {bh_ret:+7.2%}")
print(f"\n  Shortfall vs buy & hold (breaker off): "
      f"{pfull.total_return - bh_ret:+.2%}")

print()
print("=" * 72)
print("4. BREAKDOWN BY STRATEGY AND SYMBOL (breaker off, full period)")
print("=" * 72)
by_strat = defaultdict(lambda: [0, 0.0, 0])
by_sym = defaultdict(lambda: [0, 0.0, 0])
for t in full.trades:
    for d, k in ((by_strat, t.strategy), (by_sym, t.symbol)):
        d[k][0] += 1
        d[k][1] += t.pnl
        d[k][2] += 1 if t.is_win else 0
print(f"  {'strategy':20s}{'trades':>8}{'pnl':>12}{'win rate':>10}")
for k, (n, pnl, w) in sorted(by_strat.items(), key=lambda kv: kv[1][1]):
    print(f"  {k:20s}{n:>8}{pnl:>12,.0f}{w/n:>10.1%}")
print(f"\n  {'symbol':20s}{'trades':>8}{'pnl':>12}{'win rate':>10}")
for k, (n, pnl, w) in sorted(by_sym.items(), key=lambda kv: kv[1][1]):
    print(f"  {k:20s}{n:>8}{pnl:>12,.0f}{w/n:>10.1%}")

print()
print("=" * 72)
print("5. EXIT REASONS (breaker off)")
print("=" * 72)
reasons = defaultdict(lambda: [0, 0.0])
for t in full.trades:
    reasons[t.exit_reason][0] += 1
    reasons[t.exit_reason][1] += t.pnl
for k, (n, pnl) in sorted(reasons.items(), key=lambda kv: -kv[1][0]):
    print(f"  {n:>5}  {pnl:>12,.0f}  {k}")

print()
print("=" * 72)
print("6. COSTS")
print("=" * 72)
print(f"  total fees paid      ${pfull.total_fees:,.2f}")
print(f"  gross pnl before fees ${pfull.total_pnl + pfull.total_fees:,.2f}")
print(f"  net pnl               ${pfull.total_pnl:,.2f}")
if pfull.total_pnl:
    word = "profit" if pfull.total_pnl > 0 else "loss"
    print(f"  fees as share of the {word}: "
          f"{pfull.total_fees / abs(pfull.total_pnl):.1%}")
else:
    print("  fees as share of pnl: n/a (pnl is zero)")
