-- Agent Bridge gateway schema, version 1. Applied by bin/migrate.php, which
-- records each file in schema_migrations and never re-runs one.
-- Times are integer Unix seconds (UTC).

CREATE TABLE IF NOT EXISTS tasks (
    id               TEXT PRIMARY KEY,
    client_id        TEXT NOT NULL,
    idempotency_key  TEXT,
    schema_version   TEXT NOT NULL,
    task_type        TEXT NOT NULL,
    agent            TEXT NOT NULL,
    workspace        TEXT NOT NULL,
    operation        TEXT NOT NULL,
    requested_mode   TEXT NOT NULL,
    approval_mode    TEXT NOT NULL,      -- the effective mode policy granted
    prompt           TEXT NOT NULL,
    state            TEXT NOT NULL,
    attempts         INTEGER NOT NULL DEFAULT 0,
    max_attempts     INTEGER NOT NULL DEFAULT 2,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    last_error       TEXT,
    created_at       INTEGER NOT NULL,
    updated_at       INTEGER NOT NULL,
    UNIQUE (client_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS tasks_state_created ON tasks (state, created_at);

CREATE TABLE IF NOT EXISTS task_events (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL REFERENCES tasks (id),
    kind       TEXT NOT NULL,
    message    TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS task_events_task ON task_events (task_id, seq);

CREATE TABLE IF NOT EXISTS task_results (
    task_id      TEXT PRIMARY KEY REFERENCES tasks (id),
    ok           INTEGER NOT NULL,
    summary      TEXT NOT NULL,
    body         TEXT NOT NULL,
    performer    TEXT NOT NULL,
    completed_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS worker_leases (
    task_id     TEXT PRIMARY KEY REFERENCES tasks (id),
    worker_id   TEXT NOT NULL,
    lease_hash  TEXT NOT NULL,           -- sha256 of the lease token; the token itself is never stored
    expires_at  INTEGER NOT NULL,
    claimed_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_registry (
    name         TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    description  TEXT NOT NULL,
    operations   TEXT NOT NULL,          -- JSON array
    workspaces   TEXT NOT NULL,          -- JSON array of workspace names, never paths
    enabled      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS api_tokens (
    id         TEXT PRIMARY KEY,
    role       TEXT NOT NULL CHECK (role IN ('client', 'worker')),
    token_hash TEXT NOT NULL UNIQUE,     -- sha256 of the bearer token
    label      TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    revoked    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS audit_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         INTEGER NOT NULL,
    actor      TEXT NOT NULL,
    action     TEXT NOT NULL,
    task_id    TEXT,
    outcome    TEXT NOT NULL,
    detail     TEXT NOT NULL             -- JSON; never prompts, results or tokens
);
