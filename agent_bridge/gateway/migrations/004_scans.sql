-- Scanner bridge: what each bridge-started scan produced, uploaded by the worker.
-- Additive only. Bytes live in scans_dir (outside the web root), named by sha256;
-- these tables hold only hashes, sizes, extracted text and the scan's record.

CREATE TABLE IF NOT EXISTS scans (
    scan_id           TEXT PRIMARY KEY,        -- the dashboard's own durable id (scan_<ULID>)
    task_id           TEXT NOT NULL UNIQUE REFERENCES tasks (id),
    client_id         TEXT NOT NULL,           -- copied from the task: who may read it
    scanner           TEXT NOT NULL CHECK (scanner IN ('window', 'freezer')),
    status            TEXT NOT NULL,
    record            TEXT NOT NULL,           -- JSON: the dashboard's public record, Mazda detail included
    original_sha256   TEXT,
    original_mime     TEXT,
    original_bytes    INTEGER,
    original_stored   INTEGER NOT NULL DEFAULT 0,
    page_count        INTEGER NOT NULL DEFAULT 0,
    created_at        INTEGER NOT NULL,
    updated_at        INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS scans_client_created ON scans (client_id, created_at);

CREATE TABLE IF NOT EXISTS scan_pages (
    scan_id    TEXT NOT NULL REFERENCES scans (scan_id),
    page       INTEGER NOT NULL,
    rendition  TEXT NOT NULL CHECK (rendition IN ('preview', 'full')),
    sha256     TEXT NOT NULL,
    mime       TEXT NOT NULL,
    width      INTEGER NOT NULL,
    height     INTEGER NOT NULL,
    bytes      INTEGER NOT NULL,
    PRIMARY KEY (scan_id, page, rendition)
);

CREATE TABLE IF NOT EXISTS scan_texts (
    scan_id    TEXT NOT NULL REFERENCES scans (scan_id),
    page       INTEGER NOT NULL,
    source     TEXT NOT NULL,
    text       TEXT NOT NULL,
    PRIMARY KEY (scan_id, page)
);
