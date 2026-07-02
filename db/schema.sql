-- ---------------------------------------------------------------------------
-- pgvector extension + code_chunks table (RAG vector store)
--
-- IMPORTANT: VECTOR(3072) matches the `gemini-embedding-001` embedding model.
-- If you use a different embedding model, change the dimension to match:
--     models/gemini-embedding-001  -> 3072   (default)
--     models/text-embedding-004    -> 768
--     models/embedding-001         -> 768
-- The dimension here MUST equal GEMINI_EMBEDDING_MODEL's output size, or
-- inserts will fail with a dimension-mismatch error.
--
-- No ivfflat/hnsw ANN index is created: pgvector's ANN indexes only support
-- up to 2000 dimensions, so 3072-dim vectors use exact search (ORDER BY
-- embedding <=> $1). That is fine for repo-sized corpora.
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS code_chunks (
    id            SERIAL PRIMARY KEY,
    repo          TEXT NOT NULL,
    file_path     TEXT NOT NULL,
    chunk_text    TEXT NOT NULL,
    embedding     VECTOR(3072),
    checksum      TEXT NOT NULL,

    -- Citation metadata (populated by services/chunker.py)
    start_line    INTEGER,
    end_line      INTEGER,
    function_name TEXT,

    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS code_chunks_repo_file_idx ON code_chunks (repo, file_path);


CREATE TABLE IF NOT EXISTS reviews (
    id              SERIAL PRIMARY KEY,
    pr_url          TEXT,
    repo            TEXT NOT NULL,
    pr_number       INTEGER,
    verdict         TEXT,
    risk_score      INTEGER,
    overall_summary TEXT,
    confidence      FLOAT,
    raw_json        JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS review_comments (
    id          SERIAL PRIMARY KEY,
    review_id   INTEGER REFERENCES reviews(id) ON DELETE CASCADE,
    severity    TEXT,
    category    TEXT,
    file        TEXT,
    function    TEXT,
    line_number INTEGER,
    issue       TEXT,
    suggestion  TEXT,
    confidence  FLOAT,
    evidence    TEXT,
    existing_pattern_file     TEXT,
    existing_pattern_function TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agent_traces (
    id          SERIAL PRIMARY KEY,
    review_id   INTEGER REFERENCES reviews(id) ON DELETE CASCADE,
    stage       TEXT,       -- "context_agent", "review_agent", "retry_agent"
    tool_name   TEXT,
    tool_query  TEXT,
    tool_result TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS reviews_repo_idx ON reviews (repo);
CREATE INDEX IF NOT EXISTS review_comments_review_id_idx ON review_comments (review_id);
CREATE INDEX IF NOT EXISTS agent_traces_review_id_idx ON agent_traces (review_id);

-- ---------------------------------------------------------------------------
-- code_chunks citation metadata (backfill)
-- The table is created above. These ALTERs are kept for databases whose
-- code_chunks table predates the citation columns. Additive and nullable, so
-- old rows keep working until the repo is re-ingested.
-- ---------------------------------------------------------------------------
ALTER TABLE code_chunks ADD COLUMN IF NOT EXISTS start_line     INTEGER;
ALTER TABLE code_chunks ADD COLUMN IF NOT EXISTS end_line       INTEGER;
ALTER TABLE code_chunks ADD COLUMN IF NOT EXISTS function_name  TEXT;

-- ---------------------------------------------------------------------------
-- Chat conversation memory
-- A session groups a multi-turn conversation about one repo. Messages store
-- the running transcript so follow-up questions can reuse earlier context.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chat_sessions (
    id           SERIAL PRIMARY KEY,
    session_key  TEXT UNIQUE NOT NULL,   -- caller-supplied id, for example "default" or a UUID
    repo         TEXT NOT NULL,
    title        TEXT,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id           SERIAL PRIMARY KEY,
    session_id   INTEGER REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role         TEXT NOT NULL,          -- "user" | "assistant"
    content      TEXT NOT NULL,
    citations    JSONB,                  -- [{file_path, function_name, start_line, end_line}]
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS chat_sessions_key_idx ON chat_sessions (session_key);
CREATE INDEX IF NOT EXISTS chat_messages_session_id_idx ON chat_messages (session_id, id);