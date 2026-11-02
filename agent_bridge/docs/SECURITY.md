# Agent Bridge — Security design

- **No inbound ports at home.** The worker only makes outbound HTTPS calls.
- **No shell from JSON.** Tasks name an `agent`, `workspace` and `operation`
  from allow-lists; the prompt is passed to Codex on **stdin**, never as argv.
  The Codex model name must match `[A-Za-z0-9._-]{1,64}`.
- **Read-only by default, writes scoped, both enforced twice.** Each operation
  has one mode: `inspect/explain/search_memory` run `read_only`, and `implement`
  runs `workspace_write`. A requested `approval_mode` that differs is refused,
  never reinterpreted. The gateway allows `implement` only for an agent whose
  card lists it and only in that card's `write_workspaces` (live: `codex-writer`
  in `bridge_sandbox`). The worker checks again against its own
  `workspaces.json`, which names each workspace's directory. It refuses an
  unregistered name, a path, or a symlink escape. Read tasks run Codex with
  `--sandbox read-only --ephemeral`. Write tasks run `-s workspace-write`
  inside bwrap, with only that workspace writable. They hold a per-workspace
  lock and never reset or clean what is already there. A task cannot change
  the gateway config or `workspaces.json`, because neither is inside a write
  workspace.
- **Switching writes off.** Remove `'implement'` from `allowed_operations` and
  `'workspace_write'` from `allowed_modes` in `~/agent_bridge_data/bridge.php`.
  Read-only keeps working. To shut the worker side too, set
  `"writes": false` for the workspace in
  `lancedb_memory/agent_bridge_worker/workspaces.json`.
- **Agent gating.** The `codex` card accepts tasks only from `chatgpt-bridge`;
  receptionists (no `accepts_from` entry) are refused.
- **Tokens.** Client and worker tokens are separate roles (a worker token gets
  403 on client routes; no token gets 401). Stored as hashes, printed once by
  `issue_token.php`, revocable with `--revoke`. The worker reads its token from
  a 0600 file outside the repo; `HttpTaskGateway` refuses `http://` and never
  shows the token in `repr`.
- **Data placement.** SQLite DB and config live outside `public_html`;
  `.htaccess` routes every request to `index.php`, denies dotfiles and
  turns off directory listings. No secrets in the repo.
- **Leases.** Each claim carries a lease token; heartbeats extend it; an
  expired lease requeues (bounded by `max_attempts`) and the old worker's
  results are rejected.
- **Audit.** Submits, claims, cancels, completions and failures are written to
  `audit_events` (no prompt bodies, no tokens). Rejected auth attempts get a
  401 but are **not** audited yet; add that before going public.
- **Codex read scope (fixed 2026-10-31).** Codex's `--sandbox read-only`
  blocks writes, not reads, so the codex card now sets `"jail": "bwrap"` and
  `CodexCliExecutor` runs it inside bubblewrap (`agent_comms/executors/jail.py`).
  It sees `/usr` and `/etc` read-only, the workspace read-only minus its
  private data, `~/.codex` (its login), and the answer's scratch dir. The rest
  of `/home`, `/mnt/c` and the old `/tmp` are not there; the environment is
  cleared. Hidden inside the workspace: `.claude`, `mailbox`, `adws/adw_data`,
  LanceDB data, the nexus, everything git ignores (widened to whole
  directories), and anything named like a secret or database (`.env`,
  `*credentials*`, `*_token`, `*.pem`, `*.db`, `*.sqlite`). If bubblewrap or
  git fails, the task fails; codex is never started unjailed. Verified with a
  live codex run and canaries (outside-home file, ignored data, `.env`, write
  attempt, env keys): all blocked, workspace code still readable. Trade-off:
  codex can no longer read other repos such as `~/letta-code`.
- **Polite traffic.** The worker names itself
  (`User-Agent: agent-bridge-worker/1.0`; HostGator's mod_security returns 406
  to urllib's default). It runs only when invoked and heartbeats every
  lease/3 = 40 s. HostGator resets SSH after rapid logins, so do server
  maintenance in a single session.

## ChatGPT connector sign-in (OAuth 2.1, added 2026-10-31)

ChatGPT's MCP connector signs in to the gateway with OAuth (`src/OAuth.php`, `migrations/002_oauth.sql`).
This is the gateway's *own* OAuth. Codex's ChatGPT login is unrelated and runs the other way.

- **Discovery.** A 401 from `/mcp` carries `WWW-Authenticate: Bearer resource_metadata=...`. The RFC 9728 and RFC 8414 metadata are served at `/.well-known/oauth-protected-resource[/api/agent-bridge/v1/mcp]` and `/.well-known/oauth-authorization-server`.
- **Registration (RFC 7591).**
  - Only these redirect URIs are accepted: `https://chatgpt.com/connector_platform_oauth_redirect` and `https://chatgpt.com/connector/oauth/<id>`.
  - Only public clients (`none`) can register, at most 50 of them. A client that never connected is pruned after a day.
- **Consent.** Signing in needs the owner passphrase.
  - It lives on Win11 at `~/.config/agent_bridge/owner_passphrase` (0600). The server keeps only its `password_hash`.
  - The consent page runs no scripts and cannot be framed. It carries the checked request as an HMAC-signed ticket that expires in 10 minutes.
  - After 5 wrong passphrases in 15 minutes, sign-in pauses for everyone. A stranger can delay EG's sign-in this way, but cannot guess the passphrase.
- **Codes.** PKCE S256 is required. A code lasts 60 s and works once; reusing it revokes the connection it made. `iss` is returned on every redirect (RFC 9207).
- **Tokens.**
  - An access token lasts 1 h.
  - A refresh token lasts 30 days and rotates on every use; replaying an old one revokes the connection.
  - Codes and tokens are stored only as sha256 hashes.
- **Policy.** An OAuth connection is a `client` Principal with its own id. It gets the same server-side read-only policy as a client token and cannot see other clients' tasks.
- **Audit.** Registrations, consent outcomes, token refusals and rejected bearers are recorded, never with a secret.
- **Admin (on the server).** `php bin/oauth_admin.php grants | revoke <id> | passphrase [config]`. The passphrase is read from stdin, never from argv.
