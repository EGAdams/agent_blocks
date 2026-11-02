-- Agent Bridge gateway schema, version 2: OAuth 2.1 for the ChatGPT connector.
-- Codes and tokens are stored only as sha256 hashes; the owner passphrase only
-- as password_hash(). Times are integer Unix seconds (UTC).

CREATE TABLE IF NOT EXISTS oauth_clients (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    redirect_uris TEXT NOT NULL,         -- JSON array; each matched an allowed pattern at registration
    created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_grants (
    id         TEXT PRIMARY KEY,         -- one per approved connection; becomes the Principal id
    client_id  TEXT NOT NULL REFERENCES oauth_clients (id),
    scope      TEXT NOT NULL,
    resource   TEXT NOT NULL,            -- the MCP URL the tokens are good for
    created_at INTEGER NOT NULL,
    revoked    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS oauth_codes (
    code_hash      TEXT PRIMARY KEY,
    client_id      TEXT NOT NULL REFERENCES oauth_clients (id),
    redirect_uri   TEXT NOT NULL,
    code_challenge TEXT NOT NULL,        -- PKCE S256
    scope          TEXT NOT NULL,
    resource       TEXT NOT NULL,
    expires_at     INTEGER NOT NULL,
    grant_id       TEXT                  -- set on redemption; a second redemption revokes this grant
);

CREATE TABLE IF NOT EXISTS oauth_tokens (
    token_hash TEXT PRIMARY KEY,
    grant_id   TEXT NOT NULL REFERENCES oauth_grants (id),
    kind       TEXT NOT NULL CHECK (kind IN ('access', 'refresh')),
    expires_at INTEGER NOT NULL,
    used       INTEGER NOT NULL DEFAULT 0  -- refresh tokens are single-use
);
CREATE INDEX IF NOT EXISTS oauth_tokens_grant ON oauth_tokens (grant_id);

CREATE TABLE IF NOT EXISTS oauth_owner (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    passphrase_hash TEXT NOT NULL,
    updated_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_failures (
    at INTEGER NOT NULL                  -- one row per wrong passphrase, kept for the lockout window
);

CREATE TABLE IF NOT EXISTS oauth_keys (
    name   TEXT PRIMARY KEY,
    secret TEXT NOT NULL
);
INSERT OR IGNORE INTO oauth_keys (name, secret) VALUES ('ticket', lower(hex(randomblob(32))));
