# shared/ — the research/execution boundary

Two directories, both read by the engine and written by something else. This
separation is the point: the research side has no execution rights, and the
execution side makes no network or LLM calls.

## `signals/conviction.json`

Written out-of-band by a research process, read by `agent_trader.gate`. Schema:

```json
{
  "generated_at": "2026-10-05T12:00:00",
  "signals": {
    "SPY": {
      "as_of": "2026-10-05T11:55:00",
      "valid_until": "2026-10-05T12:25:00",
      "conviction": 0.85,
      "event_risk_probability": 0.12
    }
  }
}
```

`as_of` / `valid_until` are enforced strictly: a record stamped in the future is
rejected as lookahead, and a record past `valid_until` is rejected as stale.
Anything missing, malformed or unparseable denies the trade — see
`docs/ARCHITECTURE.md` for the full table.

Use `agent_trader.gate.write_ledger`, which writes atomically via `os.replace`
so a half-written ledger is never observable.

## `control/KILL`

Create this file to deny all new entries immediately:

```bash
touch shared/control/KILL     # stop
rm shared/control/KILL        # resume
```

A plain file is deliberate: any human with a shell can create it, with no
dependency on this process being alive or healthy. It is gitignored, because a
committed KILL file would silently disable trading for everyone who cloned the
repo.
