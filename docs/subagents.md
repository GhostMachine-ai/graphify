# Subagents

This repo ships three Claude Code subagents in `.claude/agents/`. This page explains when
each one fires, what it buys, and — just as importantly — when not to reach for one.

A subagent is a scoped prompt plus a tool allowlist that runs in its own context and reports
back. The value is not that it is smarter; it is that repo-specific knowledge gets loaded
**at the moment it applies**, instead of depending on someone remembering to read
`MIGRATION.md` or `ci.yml` first.

Each of these exists because the knowledge it carries is non-obvious, load-bearing, and
currently only discoverable by reading a file most people do not know to open.

---

## `skillgen-guard`

**Fires when:** a change touches — or needs to touch — `graphify/skill*.md` or
`graphify/always_on/*.md`, when `tools.skillgen --check` fails, or when CI's `skillgen-check`
job is red.

**The problem it solves.** Those files are generated from fragments under
`tools/skillgen/fragments/`. They carry no `DO NOT EDIT` header and read like ordinary
hand-maintained markdown. Editing one directly looks correct locally and then fails CI across
five separate validators (`--check`, `--audit-coverage`, `--schema-singleton`,
`--monolith-roundtrip`, `--always-on-roundtrip`).

**What it buys.** It catches the wrong edit before the commit, redirects it to the owning
fragment, and runs all five gates rather than just the obvious one. It also knows the subtle
part: three of those validators read blobs from `origin/v8` and **silently skip** in a shallow
clone — so a clean local run can mean nothing. Without the agent, that is a red CI run, a
confused round-trip, and usually a second red run.

**What it costs.** One extra hop for what feels like a one-line text change. Sometimes that
feels like friction. It is the cheapest of the three to be wrong about, because its failure
mode is "you re-ran the generator unnecessarily."

---

## `node-id-auditor`

**Fires when:** reviewing or writing anything that mints a node id, an edge endpoint, or a
manifest key — `extract.py`, `extractors/`, `build.py`, `manifest.py`, `ids.py` — or when a
bug mentions dangling nodes, edges vanishing on `--update`, graphs differing between machines,
or non-ASCII / macOS path handling.

**The problem it solves.** This is the most-repeated bug class in the project. Release 0.9.29
fixed absolute-path and machine-slug ids leaking into edge endpoints (#2231, #2243). Release
0.9.28 fixed four more of the same family: cross-file edges dropped for want of a
`target_file` stamp (#2211, #2213), alive files pruned as deleted because a path comparison
skipped NFC normalization (#2210), and macOS re-extracting everything because manifest keys
were not NFC-normalized (#2221). `ids.py` documents the class going back to #550 and #811.

Every instance has the same shape, and nothing systematically guards it.

**What it buys.** A reviewer that already knows the five failure signatures and, critically,
knows they are *silent* — a leaked absolute path produces a perfectly valid-looking
`graph.json` that is simply wrong on someone else's machine. Ordinary review does not catch
that because nothing looks broken.

**What it costs.** It is read-only and reports rather than fixes, so it adds a step. On a diff
that touches no id construction it will correctly find nothing, and that run was wasted.

---

## `extractor-migrator`

**Fires when:** porting a language extractor out of `graphify/extract.py` into
`graphify/extractors/` (upstream #1212), or reviewing such a port.

**The problem it solves.** `MIGRATION.md` is explicitly written so an agent can execute a port
in one session, and it carries hard invariants: verbatim moves only, one language per PR,
mandatory facade re-export, zero test edits outside the registry test. Violating any of them
produces a diff that passes tests but destroys the proof of behavior preservation, which is
the entire point of the exercise.

It also carries two traps. First, **config-driven extractors share a ~1,300-line
`_extract_generic` core and must not be ported individually** — porting one looks like it
works and is exactly the wrong move. Second, **the status table in `MIGRATION.md` is stale**:
it lists julia, verilog, markdown, objc and pascal as unmigrated when all five are already
done. An agent trusting the table picks an already-completed language.

**What it buys.** Correct language selection, correct helper classification (the shared-vs-
private `grep -c` rule that decides whether a helper goes to `base.py` or into the language
module), and the byte-identity discipline that makes the port reviewable.

**What it costs.** Real tokens — this one reads a lot of `extract.py`. It is the most
expensive of the three and the one most worth reserving for the actual task.

---

## When not to delegate

Subagents are not free, and a directory full of marginal ones stops being read.

- **One-line, obvious fixes.** A typo, a version bump, a docstring. The hop costs more than
  the work.
- **When you already know the answer.** These agents encode knowledge. If you have it, use it
  directly.
- **Broad exploration with no defined target.** "Understand this codebase" is not a job for a
  specialist agent. Read `AGENTS.md` and use the graphify graph.
- **Anything needing a decision that is yours.** Agents report and propose. Scope calls,
  API-shape choices, and release timing stay with you.
- **As a substitute for tests.** `node-id-auditor` finding nothing is not evidence of
  correctness. A regression test is.

A useful check: if you cannot say what the agent knows that you do not, you do not need it for
that task.

## Adding another one

Only add an agent when you can name a *recurring* task with *non-obvious* rules that are
currently only discoverable by reading some specific file. If the rules fit in a sentence,
they belong in `AGENTS.md` instead — that file is already loaded every session, at zero extra
cost.

Format is YAML frontmatter (`name`, `description`, optional `tools` and `model`) followed by
the system prompt. The `description` is the routing signal: write it as *when to use this*,
because that is what the dispatcher matches against. Keep `tools` as narrow as the job allows
— an auditing agent should not be able to write.
