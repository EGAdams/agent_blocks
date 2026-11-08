-- Browser downloads: the files a browser.download task saved on EG's desktop, published by the worker
-- so a client can view them (browser_view_file). Additive only. Bytes live in scans_dir beside the
-- scanner's, named by sha256 (FileBlobStore); these tables hold hashes, sizes, extracted text and pages.

CREATE TABLE IF NOT EXISTS downloads (
    file_id          TEXT PRIMARY KEY,          -- f_ + 24 hex, derived from task_id and idx (re-posting is idempotent)
    task_id          TEXT NOT NULL REFERENCES tasks (id),
    idx              INTEGER NOT NULL,
    client_id        TEXT NOT NULL,             -- copied from the task: who may view it
    name             TEXT NOT NULL,             -- as saved (unique in the download folder)
    original_name    TEXT NOT NULL,             -- as the site named it
    saved_path       TEXT NOT NULL,             -- where it is on EG's machine
    mime             TEXT NOT NULL,             -- from its bytes, not its name
    bytes            INTEGER NOT NULL,
    sha256           TEXT NOT NULL,
    original_stored  INTEGER NOT NULL DEFAULT 0,
    kind             TEXT NOT NULL,             -- image | pdf | text | document | spreadsheet | archive | binary
    page_count       INTEGER NOT NULL DEFAULT 0,
    text             TEXT NOT NULL DEFAULT '',
    text_truncated   INTEGER NOT NULL DEFAULT 0,
    note             TEXT NOT NULL DEFAULT '',
    created_at       INTEGER NOT NULL,
    UNIQUE (task_id, idx)
);
CREATE INDEX IF NOT EXISTS downloads_client_created ON downloads (client_id, created_at);

CREATE TABLE IF NOT EXISTS download_pages (
    file_id    TEXT NOT NULL REFERENCES downloads (file_id),
    page       INTEGER NOT NULL,
    rendition  TEXT NOT NULL CHECK (rendition IN ('preview', 'full')),
    sha256     TEXT NOT NULL,
    mime       TEXT NOT NULL CHECK (mime IN ('image/jpeg', 'image/png')),
    width      INTEGER NOT NULL,
    height     INTEGER NOT NULL,
    bytes      INTEGER NOT NULL,
    PRIMARY KEY (file_id, page, rendition)
);
