## graphify

This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- After modifying code files in this session, run `graphify update .` to keep the graph current (AST-only, no API cost)

Note: `graphify-out/` is generated and gitignored, so it is **absent in a fresh clone**. If it
is missing, build it with `graphify .` (or fall back to ordinary search) rather than treating
the missing directory as an error.

## Working in this repo

- **`graphify/skill*.md` and `graphify/always_on/*.md` are generated** from the fragments in
  `tools/skillgen/fragments/`. They carry no "DO NOT EDIT" header, but hand-editing them fails
  CI. Change the fragment, then run `python -m tools.skillgen` and `--bless`.
- **Node ids must be portable.** Never mint an id from an absolute path, a machine-specific
  slug, or a non-NFC string; use `graphify/ids.py` (`normalize_id` / `make_id`) rather than
  reimplementing the recipe. This is the most frequently reintroduced bug class in the
  changelog.
- **Tests get a sandboxed HOME.** `tests/conftest.py` installs an autouse `_sandbox_home`
  fixture that repoints `HOME`, `USERPROFILE`, `LOCALAPPDATA` and `Path.home` at a throwaway
  directory so installer tests can never touch a real `~/.claude`. Do not defeat it.
- Run tests with `uv run --frozen pytest tests/ -q --tb=short`. The `--frozen` flag is
  deliberate — it keeps `uv.lock` from churning.

## Subagents

Three specialist subagents live in `.claude/agents/`: `skillgen-guard`, `node-id-auditor`, and
`extractor-migrator`. See [docs/subagents.md](docs/subagents.md) for when each one applies,
what it buys, and when not to delegate.
