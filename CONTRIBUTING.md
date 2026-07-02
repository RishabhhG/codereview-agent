# Contributing to CodeReview AI Agent

Thanks for your interest in contributing! This document explains how to set up a
development environment, the conventions we follow, and how to submit changes.

By participating, you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).

## Ways to contribute

- 🐛 **Report bugs** — open an issue with clear reproduction steps.
- 💡 **Suggest features** — check the [ROADMAP](ROADMAP.md) first, then open an issue.
- 📝 **Improve docs** — READMEs, docstrings, and examples are always welcome.
- 🔧 **Send code** — bug fixes and features via pull request (see below).

## Development setup

Follow the [README setup](README.md#setup), then install in editable mode so your
changes take effect immediately:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

You'll need:

- PostgreSQL 14+ with the **pgvector** extension (a Docker one-liner is in the README).
- A **Gemini API key** in `.env` (`GEMINI_API_KEY`).

Quick smoke test — ingest and query a small repo:

```bash
codereview-agent ingest --repo-path . --repo-name local/codereview-agent
codereview-agent chat "how does the chunker split files?" --repo local/codereview-agent
```

## Project layout

See [README → Project structure](README.md#project-structure). In short:

- `agent/` — the review pipeline and chat agent.
- `services/` — chunking, embeddings, retrieval, LLM + GitHub clients.
- `db/` — schema and async Postgres access.
- `routers/` — the FastAPI webhook.
- `cli.py` — the Typer CLI and terminal rendering.

## Coding conventions

- **Style:** match the surrounding code. Keep functions small and readable; add a
  short docstring explaining *why* when the intent isn't obvious.
- **Python:** target 3.11+. Prefer `async` I/O (the codebase uses `asyncio` +
  `asyncpg` throughout). Never block the event loop with sync network/DB calls —
  wrap them in `asyncio.to_thread` like `services/llm_client.py` does.
- **Logging over prints:** use the module `logger`. Do **not** leave `print()`
  calls in library code — they corrupt the CLI's live/streamed output.
- **Comments:** keep them accurate. If you change behavior, update the nearby
  comment and any affected docstring.
- **Secrets:** never commit `.env`, `*.pem`, API keys, or real tokens.

## Regenerating the README screenshots

The terminal screenshots are generated from the real CLI rendering code:

```bash
python scripts/generate_screenshots.py
```

Re-run this if you change anything in `cli.py`'s rendering (`_render_answer`,
`_render_flow`, `_render_sources`) and commit the updated SVGs.

## Database schema changes

`db/schema.sql` is applied on startup by `init_db()`, which splits on `;` and
runs each statement. Two rules:

1. Keep statements **idempotent** (`CREATE ... IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`).
2. **No semicolons inside SQL comments** — the splitter breaks on them.

## Pull request process

1. Fork and create a topic branch: `git checkout -b fix/short-description`.
2. Make focused commits with clear messages (imperative mood, e.g. `fix: dedupe streamed output`).
3. Verify the CLI still imports and runs, and that `python scripts/generate_screenshots.py` succeeds.
4. Update `CHANGELOG.md` under the `[Unreleased]` section.
5. Open a PR describing **what** changed and **why**. Link any related issue.

We aim to review PRs promptly. Small, well-scoped PRs are reviewed fastest.

## Questions

Open a [discussion or issue](https://github.com/RishabhhG/codereview-agent/issues).
Thanks for helping make the project better! 🙌
