-- PDF uploads: a client (ChatGPT) sends a PDF to the gateway -- in base64 chunks, or as a link the gateway
-- fetches -- so the Notes worker can attach it to one of EG's notes (notes_attach_pdf). Additive only.
-- The bytes received so far are staged in the row (at most 10 MiB, emptied when finished); a finished
-- upload's bytes are in scans_dir beside the scanner's and the downloads', named by sha256 (FileBlobStore).

CREATE TABLE IF NOT EXISTS uploads (
    upload_id     TEXT PRIMARY KEY,           -- u_ + 24 hex
    client_id     TEXT NOT NULL,              -- who began it: the only client that may attach it
    name          TEXT NOT NULL,              -- the file name the client gave, made safe
    bytes         INTEGER NOT NULL,           -- declared up front; finish refuses any other size
    sha256        TEXT NOT NULL,              -- declared up front; finish refuses any other hash
    received      INTEGER NOT NULL DEFAULT 0,
    staged        BLOB NOT NULL DEFAULT x'',
    state         TEXT NOT NULL CHECK (state IN ('open', 'ready')),
    pages         INTEGER NOT NULL DEFAULT 0,
    created_at    INTEGER NOT NULL,
    completed_at  INTEGER
);
CREATE INDEX IF NOT EXISTS uploads_client_state ON uploads (client_id, state, created_at);
