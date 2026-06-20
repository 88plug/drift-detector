-- drift-detector schema. Idempotent: safe to run on every SessionStart.
-- WAL mode keeps the read-only MCP server and the Stop-hook writer from
-- blocking each other.

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- One row per scored assistant turn.
CREATE TABLE IF NOT EXISTS scores (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT    NOT NULL,
    ts              TEXT    NOT NULL,            -- ISO-8601 UTC
    profile         TEXT    NOT NULL,
    engine_version  TEXT    NOT NULL,
    score           REAL    NOT NULL,           -- 0..100, higher = worse
    threshold       REAL    NOT NULL,
    verdict         TEXT    NOT NULL,           -- 'ok' | 'drift'
    word_count      INTEGER NOT NULL DEFAULT 0,
    top_offenders   TEXT,                        -- JSON array
    components       TEXT,                       -- JSON object
    transcript_hash TEXT                         -- dedupe guard for the turn
);

CREATE INDEX IF NOT EXISTS idx_scores_session ON scores(session_id, id);
CREATE INDEX IF NOT EXISTS idx_scores_ts      ON scores(ts);
CREATE UNIQUE INDEX IF NOT EXISTS idx_scores_dedupe
    ON scores(session_id, transcript_hash)
    WHERE transcript_hash IS NOT NULL;

-- Per-session rollup, maintained by score.py on each insert. Lets /drift:status
-- and the statusline answer in O(1) without scanning every turn.
CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    first_ts     TEXT NOT NULL,
    last_ts      TEXT NOT NULL,
    turns        INTEGER NOT NULL DEFAULT 0,
    drift_turns  INTEGER NOT NULL DEFAULT 0,
    last_score   REAL    NOT NULL DEFAULT 0,
    max_score    REAL    NOT NULL DEFAULT 0,
    ewma_score   REAL    NOT NULL DEFAULT 0,    -- smoothed trend
    profile      TEXT
);

INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('schema_version', '1');
