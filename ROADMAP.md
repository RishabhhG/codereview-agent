# Roadmap

This is a living document describing where the project is headed. Priorities may
shift — see [open issues](https://github.com/RishabhhG/codereview-agent/issues)
for the latest, and [CONTRIBUTING](CONTRIBUTING.md) if you'd like to pick
something up.

## Now / near-term

- [ ] **Migrate off the deprecated `google-generativeai` SDK** to `google-genai`.
      The current SDK prints an end-of-support warning on import.
- [ ] **Line-numbered chunk context** in tool results so the model cites exact
      implementation lines instead of approximating a range.
- [ ] **`.env.example`** template and clearer first-run diagnostics for missing
      config (DB unreachable, API key absent, dimension mismatch).
- [ ] **PNG screenshots** in addition to SVG for maximum README compatibility.
- [ ] **Basic test suite** — chunker boundaries, schema `init_db()` idempotency,
      Markdown/Mermaid rendering, and citation formatting.

## Mid-term

- [ ] **Retrieval quality** — hybrid search (lexical + vector), re-ranking, and
      an option to demote comment/docstring-only chunks.
- [ ] **Evaluation harness** — a small labeled set to measure review precision
      and citation accuracy across changes.
- [ ] **More languages** — extend chunker boundary patterns (C/C++, Ruby, PHP,
      Kotlin, Swift) and improve name extraction.
- [ ] **Richer PR reviews** — inline review comments anchored to diff hunks, and
      severity/risk thresholds that gate the overall verdict.
- [ ] **Configurable models/costs** — per-task model selection and token budgets
      surfaced in config.

## Long-term

- [ ] **Web UI** for chat and review browsing on top of the existing FastAPI app.
- [ ] **Incremental / event-driven indexing** — re-embed only changed files on
      push, keeping the vector store fresh automatically.
- [ ] **Multi-provider embeddings/LLMs** — pluggable backends beyond Gemini.
- [ ] **Team features** — saved sessions, shareable answers with citations, and
      review analytics from the persisted `reviews` / `agent_traces` tables.

## Non-goals (for now)

- Being a general-purpose chatbot — the focus is grounded, cited answers about a
  specific indexed codebase.
- Replacing human review — the agent augments reviewers; it doesn't auto-merge.
