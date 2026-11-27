# Everyday Chrome relay

## Why it exists

Fifth Third trusts R46's everyday Chrome as a known device and asks Cashy's separate debug profile for phone
verification nobody can receive (2026-11-27). Chrome refuses `--remote-debugging-port` on a person's own profile,
but since Chrome 144 the person can turn on `chrome://inspect/#remote-debugging`; Chrome then writes
`DevToolsActivePort` into its user-data folder and answers a **browser** websocket only (no `/json` pages), showing
an **Allow** box for *every* new connection.

Cashy opens several connections per task (the `/json` list, one websocket per tab, the browser socket), so she
can't speak to that Chrome directly. The relay connects **once** (one Allow per Chrome start) and offers Cashy the
endpoints she already uses, so `cdp_browser/` and Cashy are unchanged; only her port is.

## Pieces

| File | Responsibility |
| --- | --- |
| `locate.py` | `PortFileLocator`: the browser websocket URL from `DevToolsActivePort`, re-read every connect |
| `upstream.py` | `UpstreamBrowser`: the one connection; numbered calls, events fanned out to listeners |
| `server.py` | aiohttp app: `/json/version`, `/json/list`, `/json/new`, `/json/activate`, `/json/close`, and the `/devtools/browser/*` and `/devtools/page/<id>` websockets |
| `../adw_everyday_chrome_relay.py` | the long-running service (systemd user unit `everyday-chrome-relay` on R46) |

A page websocket is a flattened session: the relay calls `Target.attachToTarget {flatten: true}` on the upstream
connection, adds that `sessionId` to everything the client sends, and hands back only that session's replies and
events (without the `sessionId`). Every client's command ids are renumbered upstream and restored on the reply.
When the client goes, the session is detached; when the tab goes, the client is closed.

## Rules

- Listens on `127.0.0.1` only, and refuses any request carrying an `Origin` header or a non-loopback `Host`
  (a web page, or DNS rebinding, can't reach it). Under WSL's mirrored networking that loopback is Windows' too,
  the same exposure as the debug Chrome's port 9222 has always had -- but this is the person's **whole profile**.
- Never clicks the Allow box, never closes the browser, never logs a command's parameters or a page's content:
  the log has connections, target ids and method names only.
- Connects lazily, on the first request that needs Chrome, so the Allow box appears when somebody has asked Cashy
  for something. While connected, Chrome shows "controlled by automated test software"; Fifth Third did not object
  (2026-11-27).
- When Chrome isn't running or Allow wasn't clicked, `/json/list` answers 503 with that reason, and Cashy says it.
  Cashy's launcher is off on this port, so she never falls back to the untrusted debug profile.
