# Agent Bridge — Deploy and rollback (Stage 4)

**Deployed 2026-10-31 with EG's approval.**
Base URL: `https://bridge.americansjewelry.com/api/agent-bridge/v1`
(PHP handler `ea-php85`; 44/44 gateway tests also pass under ea-php83 and ea-php85 on the server).
Tokens: one worker (`wsl-desktop-2obsqmc`) and one client (`eg-chatgpt`), kept
only on the Win11 box at `~/.config/agent_bridge/{worker,client}_token` (0600).
The docroot `~/bridge_public/` holds only a 7-line `index.php` shim and `.htaccess`
(must be 0644, or Apache answers 403).

Live checks: no token → 401; worker token on a client route → 403; agents
listed; 5 MCP tools; `modify_code` refused with 403; first task
`t_70adf940157455949a446ed3` succeeded via the home worker and was read back
through MCP `get_agent_task_result`.

The worker runs on Win11 as the user service `agent-bridge-worker`. EG approved it, and it was
installed and enabled on 2026-10-31. The unit file is in `agent_bridge/systemd/`.
The service runs `adw_bridge_worker.py --watch`:
- When idle it checks every 30 s, and each check is one GET.
- For 5 min after a task it checks every 5 s.
- After a 429/503 it waits 1 h.
- After any other error it doubles its wait, from 30 s up to 15 min.
- If the token is rejected it exits with code 78 and stays stopped.

To change the timing, edit `lancedb_memory/agent_bridge_worker/poll_settings.json`.
For example, set `active_hours` to `{"start_hour": 7, "end_hour": 24}` to stop checking overnight.
Then run `systemctl --user restart agent-bridge-worker`.
Logs: `journalctl --user -u agent-bridge-worker`.
To stop it for good: `systemctl --user disable --now agent-bridge-worker`.

To run it by hand instead (once per call):

```bash
cd ~/agent_blocks && BRIDGE_WORKER_TOKEN_FILE=~/.config/agent_bridge/worker_token \
  lancedb_memory/.venv/bin/python adws/adw_bridge_worker.py \
  --url https://bridge.americansjewelry.com/api/agent-bridge/v1 --drain
```

## Placement

- Subdomain `bridge.americansjewelry.com` with docroot
  `/home1/tinman72/bridge_public/` → contains only `index.php` + `.htaccess`.
- Code: `/home1/tinman72/agent_bridge/gateway/` (outside any docroot).
- Data + config: `/home1/tinman72/agent_bridge_data/` (mode 700):
  `bridge.php` (config), `bridge.sqlite`.
- PHP handler for that subdomain: `ea-php83` (has pdo_sqlite + sodium). The
  code has **not** been linted under the 7.4 handler the existing site uses.

## Deploy steps (as performed)

1. Create the subdomain in cPanel; set its PHP version to 8.3.
2. `scp -r agent_bridge/gateway tinman72@americansjewelry.com:~/agent_bridge/`
3. On the server: copy `config/bridge.example.php` to
   `~/agent_bridge_data/bridge.php`, set `database` to the data dir.
4. `php ~/agent_bridge/gateway/bin/migrate.php ~/agent_bridge_data/bridge.php`
5. Point `bridge_public/index.php` at the gateway with `BRIDGE_CONFIG`.
6. Issue tokens on the server; copy the worker token into a 0600 file at home.
7. Smoke test with curl: unauthenticated → 401, `tools/list` → 5 tools.
8. Run one drain at home:
   `uv run adws/adw_bridge_worker.py --url https://bridge.americansjewelry.com/api/agent-bridge/v1 --drain`

## Rollback

Remove the subdomain (or empty `bridge_public/`), then delete
`~/agent_bridge/` and `~/agent_bridge_data/`. The existing site is not touched
by any step above. At home, `~/agent_bridge_backups/` holds the pre-bridge
`agents.json` and `factory.py`.

## Running the local prototype

```bash
cd agent_bridge/gateway && php tests/run_tests.php            # 44 tests
cd lancedb_memory && .venv/bin/python -m agent_bridge_worker.tests.test_bridge_worker
php bin/migrate.php CONFIG && php bin/issue_token.php client me CONFIG
BRIDGE_CONFIG=CONFIG php -S 127.0.0.1:8790 public/index.php
```

## Demo result (local, 2026-10-31)

Task `t_283127a626db22e2f146e7a7`, which asked Codex to inspect and explain the
Toyota voice system without changing files, was submitted over REST. Codex ran
read-only for 39 s. The gateway state went to `succeeded`, and the result was
fetched through MCP `get_agent_task_result`. Only the ADW's own
log files changed during the run.

## OAuth deploy (2026-10-31, approved by EG: "lock down Codex first, then build the OAuth")

Everything ran in one SSH session:

1. The new gateway was unpacked to `gateway.new`. The server's PHP ran its 60 tests (all passed), and only then was it swapped in.
2. `public_base` was added to `bridge.php`, and `bin/migrate.php` applied `002_oauth.sql`.
3. The owner passphrase was piped from Win11 into `bin/oauth_admin.php passphrase`.
4. The `.well-known/oauth-*` rewrite was inserted into `~/bridge_public/.htaccess` in place, keeping the ea-php85 AddHandler block and mode 644.

Backup: `~/agent_bridge_backups/pre_oauth_20261031150545/` holds the old gateway, `bridge.php`, `bridge.sqlite` and `htaccess`.

Rollback:

- Move that `gateway` back into place.
- Restore `htaccess` to `~/bridge_public/.htaccess` (mode 644).
- Restore `bridge.php` and `bridge.sqlite` (mode 600).

The OAuth tables are additive, so restoring the old sqlite is optional.

Live checks passed:

- The PRM and AS metadata documents are served.
- `/mcp` with no token returns a 401 carrying `resource_metadata`.
- `/oauth/authorize` returns a 400 HTML page for an unknown client.

## Scoped write deploy (2026-11-01, approved by EG: "Deploy and enable")

Everything ran in one SSH session from Win11, with 5 s between steps:

1. The new gateway was unpacked to `gateway.new`. The server's PHP ran its 78 tests (all passed), and only then was it swapped in.
2. Backup: `~/agent_bridge_backups/pre_write_20261101070524/` holds `gateway`, `bridge.php`, `bridge.sqlite` (VACUUM INTO) and `htaccess`.
3. `bridge.php` got three anchored edits, each matched exactly once:
   - `'implement'` added to `allowed_operations`;
   - `'workspace_write'` added to `allowed_modes`;
   - the `codex-writer` card (`implement`, `bridge_sandbox` only).

   The new file was loaded and validated before it replaced the old one (mode 600).
4. `bin/migrate.php` applied `003_write_access.sql` and seeded 3 agents.

Home side:

- `~/bridge_workspaces/bridge_sandbox` is a small git repo with one unittest check.
- `workspaces.json` marks it `"writes": true`; `agent_blocks` stays `"writes": false`.
- The Win11 backup of the worker files before the change is `~/agent_bridge_backups/pre_write/20261101_010349/live_before.tgz`.

Live checks passed:

- `/mcp` with no token returns 401.
- `tools/list` shows `implement` and `workspace_write`.
- Refused at submit:
  - `implement` on `codex`;
  - `codex-writer` in `agent_blocks`;
  - a mode that doesn't match the operation, in both directions;
  - a path as the workspace;
  - an idempotency key reused for a different task.
- A duplicate submit with the same key gives the same task (`replayed: true`).
- `t_4d935c3ef62bc55dfdb86789`: read-only inspect succeeded, and agent_blocks was unchanged.
- `t_7451a063ff8bc63cec387182`: implement created `notes/bridge_e2e_20261101.md` in `bridge_sandbox`. Its result held the full contents, the diff, a local patch and a passing check.

Rollback:

- Move `pre_write_*/gateway` back.
- Restore `bridge.php` (mode 600).
- Restoring `bridge.sqlite` is optional; migration 003 only adds tables and columns.

To switch writes off but keep read-only, see SECURITY.md, "Switching writes off".

## letta_code write access (2026-11-01, requested by EG)

`codex-writer` may `implement` in `letta_code` (`/home/adamsl/letta-code`, the live dashboard checkout).
The read-only `codex` card is unchanged, and `bridge_sandbox` still works.

- Server: `bridge.php` codex-writer `workspaces` and `write_workspaces` gained `letta_code`, then `migrate.php`
  re-seeded the registry. Backup: `~/agent_bridge_backups/pre_letta_write_20261101191834/`.
- Win11: `workspaces.json` sets `letta_code` to `"writes": true` and lists it in `extra_write_paths`, the only
  exception to "directly under write_root". The codex-writer card names it in `write_roots`, and the executor
  jails a write to that one directory. Backup: `~/agent_bridge_backups/letta_write_20261101_201646/before.tgz`.
- No post-write checks are configured for letta_code yet, so results say so.

Rollback: restore `bridge.php` (mode 600) from the server backup and run `migrate.php`; untar the Win11 backup
from `lancedb_memory/` and restart `agent-bridge-worker`. Or, writes-off only: set `letta_code` back to
`"writes": false` and restart the worker.
