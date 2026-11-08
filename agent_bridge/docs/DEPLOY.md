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

## Worker lanes (2026-11-06, EG approved)

The queue is first-in-first-out and one worker runs one task at a time, so a long coding task used to hold
every later task (diagnostics, Machine Ops, Notes) behind it. There are now two workers:

| Unit | `--lane` | Takes | State / health files in `~/.cache/agent_bridge/` |
| --- | --- | --- | --- |
| `agent-bridge-worker` | `general` (default) | everything, including coding writes (one at a time, in order) | `poll_state.json`, `worker_health.json` |
| `agent-bridge-worker-express` | `express` | everything except coding writes (`implement` in a git-backed write workspace) | `poll_state_express.json`, `worker_health_express.json` |

- Claims stay atomic at the gateway (a second claim of one task is a 409). Coding writes only ever run on `general`,
  so they keep their order and never overlap (the per-workspace flock is still there as a second guard).
- The express worker asks the gateway to pass over coding writes: `GET /worker/tasks/next?skip=implement@letta_code,...`.
  `skip` is a list of `operation@workspace`; omitting it behaves exactly as before. The express worker **refuses to start**
  against a gateway that does not honour `skip` (it probes with a malformed list and expects `400 bad_skip`),
  because it could otherwise claim a coding task it must not run. **Deploy the gateway files before starting it.**
- Gateway files changed: `src/Contracts.php`, `src/SqliteStore.php`, `src/GatewayApp.php`
  (backup of the previous three on the host: `~/agent_bridge_backups/gateway_lane_20261106/`; rollback = copy them back,
  then stop the express worker).
- Install: `cp agent_bridge/systemd/agent-bridge-worker-express.service ~/.config/systemd/user/ &&
  systemctl --user daemon-reload && systemctl --user enable --now agent-bridge-worker-express`.
  The supervisor timer, the Worker Activity page and `adw_supervisor` each watch both lanes; a lane whose unit is not
  installed is shown as "not installed", not as a failure.

## 2026-11-08 — the ChatGPT relay and browser downloads

Deployed with EG's approval. The gateway tree on the host was replaced (`~/agent_bridge/gateway/`),
`migrate.php` re-run, and both workers restarted.

- **Backup first**: `~/agent_bridge_backups/pre_relay_download_20261108_084900` (gateway + `bridge.php`, 596 K).
  The replaced tree is also kept in place at `~/agent_bridge/gateway.old_20261108`.
- **Tests**: 132 gateway tests pass on the host under ea-php85, both in the staging directory and in place.
- **New**: `RelayTools.php` (nine `chatgpt_*` tools) and `browser_download` in `BrowserTools.php`.
  `migrate.php` reports "agents: 11 seeded"; `chatgpt-relay` is registered at index 10.
- **Live `tools/list`**: 45 tools — 15 `browser_*` (including `browser_download`) and 9 `chatgpt_*`.
- The browser agent's description was rewritten to list the three form operations and `download`; they had
  been missing, which is why ChatGPT could not see what it already had.
- `browser_download` is **verified end to end** through the connector: task `t_1e691faa592d251616670096`,
  adw `8255e094`, `verified: true`, a 4,871,863-byte zip with head `504b0304` on disk.
- The relay is **not** verified from either caller. `relay.ask` works from my own path (the ADW), and
  ChatGPT's nine tools and Toyota's `bridge_chatgpt` route pass their offline suites, but neither has carried
  a real message yet. Do not call those two paths verified.
- Left on the host and still to be tidied: `~/deploy_20261108_src/`, `~/deploy_20261108_tests/`,
  `~/agent_bridge/gateway.old_20261108`.

The ADW runs as a `uv run` subprocess per task, so a change under `adws/` takes effect without restarting
`agent-bridge-worker` or `agent-bridge-worker-express`.

## 2026-11-08 (evening) — downloads ChatGPT can look at

- **Backup first**: `~/agent_bridge_backups/browser_downloads_20261108T200646Z` (src, migrations, tests,
  `bridge.sqlite`).
- **Migration**: `006_downloads.sql` (`downloads`, `download_pages`), applied by `migrate.php`; additive.
- **Tests**: 137 gateway tests pass on the host under ea-php85 and php 8.3, in staging and in place.
- **Deployed md5s** (repo = host): `BrowserTools.php` `f83af6c9435b3b71c38ba7f68c578e84`,
  `DownloadStore.php` `bfe7d04f683ab9b0cb11ae66155612a3`, `GatewayApp.php` `45b3850bd9d6cfc4a7a1d70447a88caf`.
- **Live `tools/list`**: 47 tools, adding `browser_downloaded_files` and `browser_view_file`.
- **Worker**: the live box pulled the new `download_publisher.py` and both units were restarted; later
  `adws/`-only commits take effect without a restart.
- **Verified end to end through the live MCP endpoint** -- see the verification table in
  `BROWSER_CONTROL.md` (the two PLATS-38123 TIFFs, a PDF, a duplicate and two failures).

## 2026-11-08 (night) — ChatGPT is told it can edit one paragraph of a note

ChatGPT tried to change one paragraph of the deed by typing the whole 4,834-character note with
`browser_fill`, and the gateway refused it (`text must be short text`, `MAX_VALUE`). The worker has had
paragraph edits since 11-07 (`{"notes":"update", ..., "edits":[{"find","replace"}]}`, revision-checked,
read back twice), but the host's registry still described `notes` as "Notes Diagnostic ... its own
diagnostic note only (never the deed or any other note)", so ChatGPT never tried it.

- **Config only**, no code: the `notes` entry's `display_name` (now `Notes`) and `description` in
  `~/agent_bridge_data/bridge.php`, which lists read/search/update (find, line, section, append) and says
  to use it instead of `browser_fill`. Backup: `~/agent_bridge_backups/notes_description_20261108T210822Z/`.
- **Seeding**: `cd ~/agent_bridge/gateway && php bin/migrate.php ~/agent_bridge_data/bridge.php`. Pass
  the path explicitly: with no argument it looks for `gateway/config/bridge.php`, which does not exist, and
  exits 255 with no output.
- **Verified through MCP** with ChatGPT's client: `list_available_agents` shows the new text; a `notes`
  read of the deed succeeded (`t_5983028e5d774d02a4d7a273`); an `implement` update carrying a stale
  revision was refused with nothing written (`t_6cad6da7b7da6abbb79b5448`, the file's sha256 unchanged).
- **The deed edit itself** went through the dashboard's `POST /api/note-write` with `expected_revision`
  (`6eca8961…` → `fdc02858…`): one line changed, as `diff` shows. The open Notes page in Chrome picked it
  up within one 3-second poll with no reload. That paragraph lost its yellow review mark, and the other 10
  marks stayed. A full page reload showed the same.
- Rollback: copy the backup's `bridge.php` back and re-run the migrate command above.
