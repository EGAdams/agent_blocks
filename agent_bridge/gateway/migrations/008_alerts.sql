-- The gateway's own watch over its queue: when each worker last asked for work,
-- and the alerts raised when something stalls, fails or goes quiet. The gateway
-- is off EG's machine, so these keep being raised while that machine is down.
-- Additive only: no existing table or row is changed.

CREATE TABLE IF NOT EXISTS worker_contacts (
    worker_key  TEXT PRIMARY KEY,        -- token label plus lane, e.g. "bridge worker|all"
    label       TEXT NOT NULL,           -- the worker token's label; never the token
    lane_skip   TEXT NOT NULL,           -- the operation@workspace pairs the lane passes over ('' = takes everything)
    first_seen  INTEGER NOT NULL,
    last_seen   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id                 TEXT PRIMARY KEY,
    dedupe_key         TEXT NOT NULL,     -- one open alert per condition, e.g. "queued:t_..."
    kind               TEXT NOT NULL,
    sticky             INTEGER NOT NULL,  -- 1: stays until acknowledged (a failure); 0: clears when the condition does
    task_id            TEXT,
    problem            TEXT NOT NULL,
    recommended_action TEXT NOT NULL,
    since              INTEGER NOT NULL,  -- when the condition began (task creation, last contact)
    raised_at          INTEGER NOT NULL,
    last_seen          INTEGER NOT NULL,
    level              INTEGER NOT NULL DEFAULT 1,
    escalated_at       INTEGER,
    acknowledged_at    INTEGER,
    acknowledged_by    TEXT,
    ack_note           TEXT,
    resolved_at        INTEGER,
    resolution         TEXT,
    shown_count        INTEGER NOT NULL DEFAULT 0,
    last_shown_at      INTEGER,
    last_shown_to      TEXT,
    details            TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS alerts_key ON alerts (dedupe_key, resolved_at);
CREATE INDEX IF NOT EXISTS alerts_open ON alerts (resolved_at, raised_at);

CREATE TABLE IF NOT EXISTS watch_meta (
    name  TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
