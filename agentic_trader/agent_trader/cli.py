"""Command line interface.

    python -m agent_trader backtest --feed cached
    python -m agent_trader backtest --feed synthetic --conviction 0.85
    python -m agent_trader cache-status
    python -m agent_trader gate-demo
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

from agent_trader.backtest import Backtester
from agent_trader.benchmark import equal_weight_buy_and_hold
from agent_trader.config import (
    AppConfig,
    GateConfig,
    RiskConfig,
    daily_book,
    default_strategies,
)
from agent_trader.feeds import CachedFeed, FeedError, SyntheticFeed
from agent_trader.gate import write_ledger
from agent_trader.journal import write_daily_pnl, write_trades

SYNTHETIC_WARNING = """
!! SYNTHETIC DATA -- THESE NUMBERS DO NOT MEASURE STRATEGY PERFORMANCE !!
   Bars came from a geometric Brownian motion: increments are independent, so
   there is no trend to follow and no mean to revert to. Any profit or loss below
   is sampling noise plus transaction costs. This mode exists to exercise engine
   mechanics deterministically. For a meaningful result use --feed cached with
   bars refreshed through the agent's MCP data tools (see docs/DATA.md).
"""


def _build_config(args: argparse.Namespace) -> AppConfig:
    return AppConfig(
        risk=RiskConfig(
            starting_equity=args.equity,
            risk_per_trade=args.risk_per_trade,
            max_portfolio_drawdown=args.max_drawdown,
        ),
        gate=GateConfig(veto_threshold=args.threshold),
        strategies=daily_book() if args.book == "daily" else default_strategies(),
    )


def cmd_backtest(args: argparse.Namespace) -> int:
    cfg = _build_config(args)
    root = Path(args.root)

    if args.feed == "synthetic":
        feed = SyntheticFeed(seed=args.seed, n_bars=args.bars, annual_vol=0.45)
    else:
        feed = CachedFeed(root / "data" / "cache")

    bars, missing = {}, []
    for sc in cfg.strategies:
        try:
            loaded = feed.load(sc.symbol, sc.interval)
        except (FeedError, ValueError) as exc:
            missing.append(f"{sc.symbol} {sc.interval}: {exc}")
            continue
        if loaded:
            bars[sc.symbol] = loaded

    if not bars:
        print("No bars available for any configured symbol.", file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)
        return 2
    if missing:
        print("Skipped (no data):", file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)

    # Conviction can come from a real ledger on disk, or be synthesised for a
    # what-if run. A synthesised ledger is labelled as such in the output.
    ledger = None
    if args.conviction is not None:
        first = min(b[0].ts for b in bars.values())
        last = max(b[-1].ts for b in bars.values())
        ledger = {"signals": {
            s: {
                "as_of": (first - timedelta(days=1)).isoformat(),
                "valid_until": (last + timedelta(days=1)).isoformat(),
                "conviction": args.conviction,
            } for s in bars
        }}

    bt = Backtester(cfg, root=root)
    result, perf = bt.run(bars, ledger=ledger)

    if getattr(feed, "is_synthetic", False):
        print(SYNTHETIC_WARNING)
    print(f"Symbols: {', '.join(f'{s}({len(b)} bars)' for s, b in sorted(bars.items()))}")
    if ledger is not None:
        print(f"Conviction: SYNTHESISED at {args.conviction} "
              f"(threshold {cfg.gate.veto_threshold})")
    print()
    print(perf.render())

    # A strategy number without its benchmark is not a result. Always both.
    bench = equal_weight_buy_and_hold(bars, cfg.risk.starting_equity)
    print()
    print(bench.render(strategy_return=perf.total_return))

    if result.halted:
        print(f"\n!! RUN HALTED: {result.halt_reason}")
    if bt.sizing_bounds:
        print(f"\nSizing constraint that bound: {bt.sizing_bounds}")
        if bt.sizing_bounds.get("notional_cap"):
            print("  (notional_cap binding means realised per-trade risk was BELOW"
                  " the ATR target -- see RiskManager.size_detail)")
    if result.denials:
        print("\nEntries refused:")
        for reason, n in sorted(result.denials.items(), key=lambda kv: -kv[1])[:10]:
            print(f"  {n:>6}  {reason}")

    if args.out:
        out = Path(args.out)
        t = write_trades(out / "trades.csv", result.trades)
        d = write_daily_pnl(out / "daily_pnl.csv", result.equity_curve)
        print(f"\nWrote {t} and {d}")
    return 0


def cmd_cache_status(args: argparse.Namespace) -> int:
    feed = CachedFeed(Path(args.root) / "data" / "cache")
    rows = feed.available()
    if not rows:
        print(f"No cached bars in {feed.cache_dir}.")
        print("Populate it with the agent's MCP data tools -- see docs/DATA.md.")
        return 1
    print(f"{'symbol':<12}{'interval':<10}{'bars':>8}  fetched_at")
    for sym, interval, n, fetched in rows:
        print(f"{sym:<12}{interval:<10}{n:>8}  {fetched}")
    return 0


def cmd_gate_demo(args: argparse.Namespace) -> int:
    """Show the gate denying and permitting the same symbol."""
    from agent_trader.gate import ConvictionGate

    root = Path(args.root)
    g = ConvictionGate(GateConfig(veto_threshold=0.5), root=root)
    now = datetime.now()
    base = {
        "as_of": (now - timedelta(minutes=5)).isoformat(),
        "valid_until": (now + timedelta(minutes=30)).isoformat(),
    }
    cases = [
        ("high conviction", {**base, "conviction": 0.85}),
        ("low conviction", {**base, "conviction": 0.20}),
        ("stale", {**base, "valid_until": (now - timedelta(minutes=1)).isoformat(),
                   "conviction": 0.95}),
        ("future as_of", {**base, "as_of": (now + timedelta(minutes=5)).isoformat(),
                          "conviction": 0.95}),
        ("event risk 0.8", {**base, "conviction": 0.9,
                            "event_risk_probability": 0.8}),
        ("malformed", {"conviction": "yes"}),
    ]
    print(f"{'case':<20}{'decision':<10}reason")
    for label, rec in cases:
        d = g.confirm_entry("SPY", now, ledger={"signals": {"SPY": rec}})
        print(f"{label:<20}{'ALLOW' if d.allowed else 'DENY':<10}{d.reason}")
    d = g.confirm_entry("SPY", now, ledger={"signals": {}})
    print(f"{'no signal':<20}{'ALLOW' if d.allowed else 'DENY':<10}{d.reason}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agent_trader",
        description="Multi-asset backtest and research framework. "
                    "Research software -- not investment advice, submits no orders.",
    )
    p.add_argument("--root", default=".", help="project root (default: .)")
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("backtest", help="run a backtest")
    b.add_argument("--feed", choices=["cached", "synthetic"], default="cached",
                   help="cached = real bars from data/cache (default); "
                        "synthetic = GBM fixture, NOT a performance measurement")
    b.add_argument("--conviction", type=float, default=None,
                   help="synthesise a uniform conviction instead of reading the ledger")
    b.add_argument("--threshold", type=float, default=0.50)
    b.add_argument("--equity", type=float, default=100_000.0)
    b.add_argument("--risk-per-trade", type=float, default=0.01)
    b.add_argument("--max-drawdown", type=float, default=0.10)
    b.add_argument("--bars", type=int, default=1000, help="synthetic feed only")
    b.add_argument("--seed", type=int, default=42, help="synthetic feed only")
    b.add_argument("--book", choices=["daily", "spec"], default="daily",
                   help="daily = the book runnable against data/cache (default); "
                        "spec = the 15m/1h/4h book from the original spec")
    b.add_argument("--out", default=None, help="directory for trades.csv / daily_pnl.csv")
    b.set_defaults(func=cmd_backtest)

    c = sub.add_parser("cache-status", help="list cached bar files")
    c.set_defaults(func=cmd_cache_status)

    g = sub.add_parser("gate-demo", help="demonstrate the fail-closed gate")
    g.set_defaults(func=cmd_gate_demo)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
