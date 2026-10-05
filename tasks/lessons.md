# Lessons

## 2026-10-05 — A status report is not evidence
**What happened.** A detailed deliverable report claimed `agentic_trader` was built, tested
(30 tests, 2.872s) and shipped at `/working_dir/c_46144729e92b7656/agentic_trader/`. Checking
the filesystem showed no `/working_dir/` at all, no `agentic_trader/` anywhere, and no `tasks/`.
**Root cause.** The report came from a different session/container. Prose describing completed
work is not the work.
**Rule.** Before continuing or citing prior work, `ls` the path. Never restate another session's
metrics as verified. Re-run the suite or say the claim is unverified.

## 2026-10-05 — Synthetic random-walk data cannot validate a strategy
**What happened.** The same report cited backtests on bars from a "geometric Brownian motion
generator" as confirming the system worked. GBM is memoryless: no trend, no mean reversion, no
volume–price relationship. Momentum and mean-reversion strategies have nothing to exploit, and
the reported run was in fact a loss (-2.18%, profit factor 0.93, Sharpe -0.01).
**Root cause.** Conflating "the plumbing executes" with "the strategy has edge."
**Rule.** Synthetic data is for deterministic unit tests only. Any performance claim must come
from real bars, and a passing test suite is never presented as evidence of profitability.

## 2026-10-05 — A dependency is a CI contract, not a convenience
**What happened.** graphify's CI runs `uv run --frozen`, installing strictly from the committed
`uv.lock`. Adding numpy/pandas to a sub-package would churn the lock and break that invariant.
**Rule.** In this repo, new code is standard-library only unless the user explicitly accepts a
lockfile change. Check how CI installs before reaching for a dependency.

## 2026-10-05 — MCP tools belong to the agent, not to the runtime
**What happened.** The data design implied the engine calls market-data APIs directly. The
RobinHood/LLMQuant MCP tools are available to the agent in-session, not to a spawned Python
process.
**Rule.** Real data crosses that boundary through an explicit cache file on disk. Document the
boundary in the code; do not write docstrings implying a live call the library cannot make.

## 2026-10-05 — A float guard for "exactly zero" usually does not fire
**What happened.** `sharpe()` guarded with `if sd == 0: return 0.0` and still
returned 8.68e16 for a constant return series. `sum([0.01]*10)/10` is
0.009999999999999998, not 0.01, so deviations are ~1e-18, the variance is a
denormal, and the mean/sd ratio explodes.
**Rule.** Never test a computed float against exact zero as a degeneracy guard.
Compare the spread against the magnitude of the quantity it scales:
`sd <= max(abs(mean), 1.0) * 1e-12`.

## 2026-10-05 — Assert the accounting identity, or the numbers are decoration
**What happened.** `ending_equity` differed from `starting_equity + sum(pnl)` by
$291.22 — the sum of entry fees, charged to cash but omitted from `trade.pnl`.
Every performance metric was quietly wrong.
**Rule.** Any ledger-like system gets a conservation test asserted to the cent, as
a test and not a one-off check. For this engine the invariant is
`ending == starting + sum(trade.pnl)`.

## 2026-10-05 — Annualisation must count real observations, not nominal spacing
**What happened.** Inferring periods/year from the median bar gap read a year of
SPY 15-minute bars as 35,064 periods. Only ~6,552 exist: the market is closed
overnight and at weekends. Sharpe would have been inflated by ~2.3x on every
equity instrument.
**Rule.** Annualise by observed marks divided by elapsed calendar time. Gaps in
the session calendar then absorb themselves. Also mark equity once per timestamp,
never once per (symbol, bar) event, or the series is not uniform in time.

## 2026-10-05 — Two risk constraints means reporting which one bound
**What happened.** The ATR sizing rule was documented as giving "constant dollar
risk" of 1%. It does not: a notional cap applies too, and for every realistic
instrument the cap binds first, so realised risk was $168-$632 against a $1,000
target. The docstring was a lie my own verification caught.
**Rule.** When two limits can bind, return which one did. A system that reports a
risk budget it is not actually taking is misreporting its own risk.

## 2026-10-05 — Always report the benchmark beside the strategy
**What happened.** The book returned -5.89%, which reads as a mild
disappointment. Equal-weight buy-and-hold on the same instruments over the same
window returned +60.75%. The real result is a 67-point shortfall, and the strategy
number alone concealed it.
**Rule.** No strategy return is ever reported without its benchmark. It is now
computed by the same code path that prints the performance report, so the two
cannot be separated.

## 2026-10-05 — Hand-exercising a CLI is not testing it
**What happened.** A bot audit flagged "CLI changes may ship without shell or
end-to-end coverage." It was right. Every CLI path had been run by hand and all
worked, but nothing automated covered argument parsing, exit codes, or stdout, so
a regression in `__main__.py` or an exit status would have passed silently.
**Rule.** A CLI gets subprocess-level tests that assert exit codes and output, not
just in-process calls to `main()`. Added `tests/test_cli.py` (16 tests, 192 total).

## 2026-10-05 — Assert on normalized text, not on a particular line break
**What happened.** A CLI test asserted `"not investment advice" in stdout` and
failed: argparse hard-wraps its description, so the string appeared as
`"not\ninvestment advice"`. The disclaimer was present; the matcher was brittle.
**Rule.** When asserting against formatted CLI output, collapse whitespace first
(`" ".join(text.split())`). Otherwise the test is coupled to terminal width.

## 2026-10-05 — Intra-bar ordering is a lookahead surface, and unit tests cannot see it
**What happened.** The engine ratcheted the trailing stop using a bar's own high
and then tested that raised stop against the same bar's low. That assumes the high
preceded the low, which OHLC does not record. For O=109 H=110 L=99 on a long
stopped at 98 with a 2-ATR trail, it lifted the stop to 108 and "stopped out" at
108 — booking a +800 profit and calling it a stop exit. A trailing stop cannot
fill above its own trail level; that was the tell. Cost: 9.15 percentage points on
the real-data result, and the sign of the headline number flipped from -5.89% to
+3.26%.
**Root cause.** Two correct components composed in the wrong order. The whole
192-test suite passed under *either* ordering: `test_execution.py` tested
`update_extreme` and `stop_hit` in isolation, `test_risk.py` tested `trail_stop`
in isolation, and nothing tested the composition inside the engine loop.
**Rule.** A no-lookahead test that mutates *future* bars cannot catch intra-bar
leakage, because the bug lives inside one bar. Any state that both updates from a
bar and is tested against that bar needs an explicit ordering test at the
*engine* level, and that test must be proven to fail when the order is reverted.

## 2026-10-05 — A regression test is worthless until you watch it fail
**What happened.** After fixing the ordering I wrote three regression tests and
all 206 passed — then passed again with the bug deliberately reintroduced. Two
were unit tests calling the components in the right order by hand, which cannot
detect the engine composing them wrongly: I had reproduced the exact blind spot I
was trying to close. A third asserted "a long stop exit cannot fill above entry",
which is simply false for a *trailing* stop; 8 of 32 long stop exits legitimately
do that.
**Rule.** Never add a regression test without reverting the fix and confirming it
fails. An invariant must be checked against reality before being asserted. What
finally discriminated was a golden test over frozen committed data.

## 2026-10-05 — A no-op str.replace is silent
**What happened.** Four edits to `docs/RISKS.md` did nothing, because the
replacement strings used ASCII hyphen `-` where the file contained Unicode minus
`−` (U+2212). `str.replace` returns the unchanged string and raises nothing, so
the script reported success while the stale figures stayed on disk.
**Rule.** After a scripted replacement, grep for the old value to confirm it is
gone. For anything beyond a couple of substitutions, rewrite the file instead of
patching it.
