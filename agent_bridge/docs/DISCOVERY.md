# Agent Bridge — Discovery report (Stage 1)

Date: 2026-10-31. Host inspected: DESKTOP-2OBSQMC (Win11 WSL), plus the
HostGator account behind americansjewelry.com (read-only inspection).

## What exists

| Area | Finding |
| --- | --- |
| Website | HostGator shared hosting (gator4143, user `tinman72`), jailshell, no root, no long-running processes. Web PHP for the existing `libraries/local-php-api` app is 7.4; `ea-php83` is installed with `pdo_sqlite` and `sodium`. |
| Home machine | WSL Python 3.14; PHP 8.5.4 CLI (`php8.5-sqlite3` was installed with apt for local testing). Receptionist sidecars on 8767–8772, dashboard on 8765. |
| Agent plumbing | `lancedb_memory/agent_comms/` (the post office): durable file mailboxes, agent cards in `agents.json`, `accepts_from` gating, cancel, event log. Reused rather than duplicated. |
| Codex CLI | `/usr/local/bin/codex` 0.144.5 with a ChatGPT login. **`~/.codex/config.toml` names `gpt-6-luna`, which the ChatGPT account rejects**; the bridge card passes `-m gpt-5.6-luna` and leaves your config alone. |

## ChatGPT feasibility (verified from OpenAI help article 12584461, not tested live)

- Remote MCP servers ("connectors"/apps) with **write actions** are available in
  **Developer mode, on the web**, for Business / Enterprise / Edu (beta). Plus/Pro
  availability must be checked on EG's account; it is not assumed.
- Authentication is OAuth (the server must support `offline_access`) or none.
  A static bearer token is not one of the offered options → **Stage 5 needs an
  OAuth authenticator adapter** behind `ITaskAuthenticator`.
- **Voice mode** is not documented as calling MCP tools. Expect to dictate in
  voice, then have a text turn call the tool. Not yet verified.
- PHP can implement MCP: it is JSON-RPC 2.0 over HTTP POST (Streamable HTTP,
  single-response form). The gateway's `/mcp` route answers `initialize`,
  `tools/list` and `tools/call` and was exercised locally.
- Ordinary ChatGPT browsing cannot make authenticated POSTs. The fallback is the
  authenticated REST API (curl, a phone shortcut, or a small form behind login).

## What is NOT demonstrated

No real ChatGPT → gateway round trip. Nothing is deployed to the website.
