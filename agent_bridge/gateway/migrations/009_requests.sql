-- Request supervision: the user's request as its own record, above the tasks.
-- On 2026-11-10 EG asked ChatGPT to print the final deed and affidavit; three
-- printer_list checks succeeded, no print was ever submitted, and nothing
-- noticed, because every watch looked at tasks and no task was stuck. A request
-- row is what the watch now holds to account: it stays until it is completed
-- with evidence, failed or cancelled with a reason. Additive only.

CREATE TABLE IF NOT EXISTS requests (
    id               TEXT PRIMARY KEY,         -- rq_ and 24 hex digits
    client_id        TEXT NOT NULL,
    client_label     TEXT NOT NULL,
    idempotency_key  TEXT,
    title            TEXT NOT NULL,
    detail           TEXT NOT NULL DEFAULT '',
    agents           TEXT NOT NULL DEFAULT '[]',  -- JSON: the agents whose tasks belong to it ([] = any)
    deliverables     TEXT NOT NULL DEFAULT '[]',  -- JSON: [{key, label, agent, operation, match}]
    state            TEXT NOT NULL,               -- open, completed, failed, cancelled
    phase            TEXT NOT NULL,               -- queued, running, waiting, blocked, completed, failed, cancelled
    blocked_on       TEXT,                        -- the declared prerequisite it waits for
    blocked_owner    TEXT,                        -- who has to clear it (e.g. EG)
    progress_note    TEXT,
    created_at       INTEGER NOT NULL,
    updated_at       INTEGER NOT NULL,
    last_progress_at INTEGER NOT NULL,            -- polls, status checks and empty heartbeats never move this
    closed_at        INTEGER,
    outcome          TEXT,
    evidence         TEXT NOT NULL DEFAULT '[]',
    UNIQUE (client_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS requests_open ON requests (state, client_id, created_at);

CREATE TABLE IF NOT EXISTS request_tasks (
    task_id    TEXT PRIMARY KEY,                  -- a task belongs to at most one request
    request_id TEXT NOT NULL,
    linked_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS request_tasks_request ON request_tasks (request_id);

CREATE TABLE IF NOT EXISTS request_events (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    at         INTEGER NOT NULL,
    event      TEXT NOT NULL,                     -- opened, linked, no_progress, warning, timed_out, escalated, ...
    dedupe     TEXT,                              -- one event per stall: "<event>@<last_progress_at>"
    detail     TEXT NOT NULL DEFAULT '{}',
    UNIQUE (request_id, dedupe)
);
CREATE INDEX IF NOT EXISTS request_events_request ON request_events (request_id, seq);

-- Which MCP tools each client called, kept for a day: three status checks with no
-- action and no open request is the loop of 2026-11-10, and it is now an alert.
CREATE TABLE IF NOT EXISTS client_calls (
    at           INTEGER NOT NULL,
    client_id    TEXT NOT NULL,
    client_label TEXT NOT NULL,
    tool         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS client_calls_client ON client_calls (client_id, at);
