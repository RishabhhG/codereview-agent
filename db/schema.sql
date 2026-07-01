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