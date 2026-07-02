# CodeReview AI Agent

An AI-powered code review and codebase Q&A agent built on **RAG** (retrieval-augmented generation) over your source code. It can:

- 🔎 **Chat with your codebase** — ask "how does X work?" and get answers with **inline source citations**, real code snippets, and **Mermaid execution-flow diagrams**.
- 📝 **Review a file or pull request** — a multi-agent pipeline gathers context (import graph + semantic search), reviews the diff, and produces a structured verdict with severity/category/risk scoring.
- 🤖 **Auto-review GitHub PRs** — a FastAPI webhook receives PR events (as a GitHub App), runs the review, and posts comments back.

It uses **Google Gemini** for generation and embeddings, and **Postgres + pgvector** as the vector store.

---

## Architecture

```
                        ┌─────────────────────────────────────────────┐
   ingest_repo.py  ───▶ │  chunker → embedder → Postgres/pgvector      │
   (index a repo)       │            (code_chunks table)               │
                        └─────────────────────────────────────────────┘
                                          ▲
                                          │ semantic search (retriever + MMR)
                                          │
   ┌──────────────┐   ┌───────────────────┴───────────────────┐   ┌──────────────┐
   │  CLI: chat   │   │  CLI: review / GitHub webhook          │   │  Gemini API  │
   │  (Q&A + RAG) │──▶│  context_agent → review_agent → retry  │◀─▶│  gen + embed │
   └──────────────┘   └────────────────────────────────────────┘   └──────────────┘
```

- **Ingestion** (`ingest_repo.py`): walks a repo, splits files into function-aware chunks (`services/chunker.py`), embeds them (`services/embedder.py` + `services/embeddings.py`), and stores them in `code_chunks`.
- **Retrieval** (`services/retriever.py`): cosine similarity search with optional **MMR** diversification.
- **Chat agent** (`agent/chat_agent.py`): a streaming, multi-hop tool loop with conversation memory and `@file` / `#symbol` scoping.
- **Review pipeline** (`agent/orchestrator.py`): `context_agent` → `review_agent` → `retry_agent`.
- **Server** (`main.py`, `routers/webhook.py`): GitHub App webhook for automated PR reviews.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| **Python 3.11+** | See `requires-python` in `pyproject.toml`. |
| **PostgreSQL 14+ with [pgvector](https://github.com/pgvector/pgvector)** | Required for the vector store. |
| **Google Gemini API key** | Get one from [Google AI Studio](https://aistudio.google.com/apikey). |
| **GitHub App** *(optional)* | Only needed for automated PR reviews via the webhook. |

---

## Setup

### 1. Clone and create a virtual environment

```bash
git clone <your-repo-url> codereview-agent
cd codereview-agent

python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```

### 2. Install dependencies

```bash
pip install -e .
```

This installs the package and exposes the `codereview-agent` CLI (see `[project.scripts]` in `pyproject.toml`).

### 3. Start PostgreSQL with pgvector

Easiest via Docker (image ships with the `vector` extension available):

```bash
docker run -d --name codereview-db \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=codereview \
  -p 5432:5432 \
  pgvector/pgvector:pg16
```

> Using an existing Postgres? Just make sure the pgvector extension is installed on the server. The schema runs `CREATE EXTENSION IF NOT EXISTS vector;` for you, but the extension's binaries must already be present.

### 4. Configure environment variables

Create a `.env` file in the project root:

```dotenv
# --- Required ---
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/codereview
GEMINI_API_KEY=your_gemini_api_key_here

# --- Embeddings (default is the 3072-dim model; must match the DB schema) ---
GEMINI_EMBEDDING_MODEL=models/gemini-embedding-001

# --- Review behavior ---
AGENT_MODE=pipeline          # pipeline (multi-agent, default) | react (single-agent loop)
USE_RAG_CONTEXT=true         # include retrieved codebase context in PR reviews
USE_AGENT_REVIEW=true        # use the multi-agent pipeline for webhook reviews

# --- GitHub App (only for the PR-review webhook server) ---
GITHUB_APP_ID=123456
GITHUB_PRIVATE_KEY_PATH=./your-app.private-key.pem
GITHUB_WEBHOOK_SECRET=your_webhook_secret
```

> ⚠️ **Keep secrets out of git.** `.env` and `*.pem` are already in `.gitignore`. Do **not** paste multi-line values (like a PEM key) directly into `.env` — store the key in a file and point `GITHUB_PRIVATE_KEY_PATH` at it.

### 5. Initialize the database schema

The schema (`db/schema.sql`) is **applied automatically** the first time you run `ingest` or `chat` (via `init_db()`). All statements are idempotent (`CREATE ... IF NOT EXISTS`), so it's safe to run repeatedly.

To apply it manually instead:

```bash
psql "$DATABASE_URL" -f db/schema.sql
```

This creates the `code_chunks` vector store plus the `reviews`, `review_comments`, `agent_traces`, `chat_sessions`, and `chat_messages` tables.

---

## Usage

### Ingest a repository (index it for RAG)

Before you can chat or run RAG-augmented reviews, index a repo:

```bash
codereview-agent ingest \
  --repo-path /path/to/local/repo \
  --repo-name owner/repo
```

- `--repo-path`: local directory to walk and index.
- `--repo-name`: the identifier you'll reference later (e.g. `RishabhhG/codereview-agent`).

Re-running is incremental: unchanged files (matched by checksum) are skipped.

**Supported languages:** `.py`, `.js`, `.ts`, `.tsx`, `.jsx`, `.go`, `.rs`, `.java`, `.cs`.

### Chat with the codebase

One-shot question:

```bash
codereview-agent chat "how does the context agent retrieve relevant files?" \
  --repo owner/repo
```

Interactive session (multi-turn, remembers context):

```bash
codereview-agent chat --repo owner/repo --session my-session
```

- Reference specific files with `@path/to/file.py` and symbols with `#function_name` to scope retrieval.
- `--session/-s` names a conversation so follow-up questions reuse earlier context (default: `default`).
- Answers stream with a spinner, then render as a formatted panel with source citations and (for "how does X work" questions) a Mermaid flow diagram.

### Review a single file

```bash
codereview-agent review path/to/file.py --repo owner/repo
```

Fetches codebase context and produces a structured review of the file.

### Run the PR-review webhook server

For automated GitHub PR reviews (requires the GitHub App env vars):

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

- Health check: `GET /health`
- Webhook endpoint: `POST /webhook` (verifies `X-Hub-Signature-256` against `GITHUB_WEBHOOK_SECRET`).

Point your GitHub App's webhook at `https://<your-host>/webhook`, subscribe to **Pull request** events, and grant read access to code + read/write to PRs. Behavior is controlled by `USE_RAG_CONTEXT`, `USE_AGENT_REVIEW`, and `AGENT_MODE`.

---

## Configuration reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | ✅ | — | Postgres DSN, e.g. `postgresql://user:pass@host:5432/db`. |
| `GEMINI_API_KEY` | ✅ | — | Google Gemini API key (generation + embeddings). |
| `GEMINI_EMBEDDING_MODEL` | — | `models/gemini-embedding-001` | Embedding model. **Its output dimension must match the `code_chunks.embedding` column.** |
| `AGENT_MODE` | — | `pipeline` | `pipeline` (multi-agent) or `react` (single-agent tool loop). |
| `USE_RAG_CONTEXT` | — | `false` | Include retrieved codebase context in webhook reviews. |
| `USE_AGENT_REVIEW` | — | `false` | Use the multi-agent pipeline for webhook reviews. |
| `GITHUB_APP_ID` | server only | — | GitHub App ID. |
| `GITHUB_PRIVATE_KEY_PATH` | server only | — | Path to the GitHub App's `.pem` private key. |
| `GITHUB_WEBHOOK_SECRET` | server only | — | Secret used to verify incoming webhook signatures (the server refuses to start without it). |

> The CLI (`ingest`, `chat`, `review`) needs only `DATABASE_URL` and `GEMINI_API_KEY`. The GitHub variables are required only when running the webhook server.

---

## Embedding dimensions (important)

The `code_chunks.embedding` column is `VECTOR(3072)`, matching `models/gemini-embedding-001`. **The column dimension must equal your embedding model's output size**, or inserts fail with a dimension-mismatch error.

| Model | Dimension |
|---|---|
| `models/gemini-embedding-001` (default) | **3072** |
| `models/text-embedding-004` | 768 |
| `models/embedding-001` | 768 |

If you switch to a 768-dim model, change `VECTOR(3072)` → `VECTOR(768)` in `db/schema.sql` and re-create the table (see reset below).

> **Why no `ivfflat`/`hnsw` index?** pgvector's ANN indexes support at most 2000 dimensions. At 3072 dims the table uses **exact** nearest-neighbor search (`ORDER BY embedding <=> $1`), which is perfectly fine for repo-sized corpora. If you drop to ≤2000 dims and want an ANN index, add one after ingestion.

---

## Re-indexing / resetting the vector store

`ingest` is incremental, so normally you just re-run it. To wipe and rebuild `code_chunks` from scratch (e.g. after changing embedding dimensions):

```sql
-- ⚠️ destructive: deletes all indexed chunks
DROP TABLE IF EXISTS code_chunks;
```

Then re-apply the schema (`psql "$DATABASE_URL" -f db/schema.sql` or just run `ingest` again) and re-ingest.

---

## Project structure

```
codereview-agent/
├── cli.py                  # Typer CLI: ingest / chat / review
├── ingest_repo.py          # Repo walker + ingestion entrypoint
├── main.py                 # FastAPI app (webhook server)
├── db/
│   ├── schema.sql          # All tables incl. code_chunks (pgvector)
│   ├── connection.py       # asyncpg pool + init_db()
│   ├── chat_store.py       # Chat session/message persistence
│   └── db_writer.py        # Review persistence
├── agent/
│   ├── orchestrator.py     # Review pipeline (pipeline | react)
│   ├── context_agent.py    # Gathers context: import graph + semantic search
│   ├── review_agent.py     # Produces the structured review
│   ├── retry_agent.py      # Retries/repairs low-confidence output
│   ├── chat_agent.py       # Streaming multi-hop codebase Q&A
│   ├── tools.py            # Retrieval tools exposed to the LLM
│   └── ...
├── services/
│   ├── chunker.py          # Function-aware code chunking
│   ├── embedder.py         # Embed + store chunks (checksum dedup)
│   ├── embeddings.py       # Gemini embedding client (retry/cache)
│   ├── retriever.py        # Vector search + MMR
│   ├── llm_client.py       # Gemini chat/streaming/tool-calling
│   ├── references.py       # @file / #symbol resolution
│   └── ...
└── routers/
    └── webhook.py          # GitHub PR webhook handler
```

---

## Troubleshooting

- **`extension "vector" is not available`** — pgvector isn't installed on the Postgres server. Use the `pgvector/pgvector` Docker image or install the extension for your Postgres build.
- **`expected N dimensions, not M`** on ingest — `GEMINI_EMBEDDING_MODEL` doesn't match the `code_chunks.embedding` dimension. Align them (see [Embedding dimensions](#embedding-dimensions-important)) and re-create the table.
- **`GEMINI_API_KEY is not set`** — add it to `.env`; the embedding client raises on import without it.
- **`python-dotenv could not parse statement`** — your `.env` has a line dotenv can't parse (often a multi-line value like a PEM key pasted inline). Move the key to a file and reference it via `GITHUB_PRIVATE_KEY_PATH`.
- **Chat returns "insufficient context"** — make sure you've ingested the repo under the exact `--repo-name` you're passing to `chat --repo`.

---

## Notes

- The Gemini generation model is `gemini-2.5-flash` (`services/llm_client.py`).
- Server logs are written to `logs/agent.log`.
- The project currently uses the `google-generativeai` SDK, which is deprecated upstream in favor of `google-genai`; migration is a known follow-up.
