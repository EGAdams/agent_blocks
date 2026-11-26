# Sheila mission checklist (EG, 2026-11-26, "GET SHEILA FULLY OPERATIONAL")

Persistent state for the mission. **accepted ≠ completed**: a row is `done` only with the evidence named beside it.
States: `done` (evidence) · `open` · `blocked` (why, whose) · `superseded` (by what). Times are UTC.

## Decisions recorded

| Item | Disposition | Why |
|---|---|---|
| SQMC `agent_blocks_dev`'s `adw_interface_sheila_demo.py`, its `sheila/README.md` and `test_sheila_demo_adw.py` | **superseded** -- not ported | Written when Pi was absent (capabilities build/ask/message, no Pi). Main 22eadbf already has the real executor; porting would make a second implementation. Left untouched in that checkout. |
| Supervisor todo `8d8d4aed` / Bridge task `t_a7ab6b7cc3a8fabb79e87dc3` / envelope `20261125T201929Z_8d8d4aed_from_chatgpt-bridge` | **stays failed (historical)** -- not replayed | Failed 2026-11-25T20:19:41Z on the Claude 5-hour usage limit; no Sheila run happened. Replaying would duplicate work this mission does directly. |
| Request `rq_2efbd0b987493fa1bf234914` (`sheila-finish-20261125`) | **superseded** by `rq_b0bf4a3307995bb6ac4e1b92`; **blocked** on ChatGPT (OAuth client) or EG to close | requests are scoped per client; this mission's `eg-chatgpt` token gets `not_found` for it, and the scoping is not worked around. Close text is in `SHEILA.md` |
| Request `rq_e7f95a093cc0deb6a74d886d` | **duplicate** of `rq_2efbd…`; same block | same goal, same owner |
| Mission request | `rq_b0bf4a3307995bb6ac4e1b92` (client `eg-chatgpt`) | every acceptance task declares it (link `declared`) |
| Sheila's `accepts_from` widened to chatgpt/toyota/sydney-bridge | **done** (agents.json) | the mission (EG) requires Bridge tasks; served only by BATI's machine lane (`Machine.lane_agents` keeps the SQMC lanes from building her) |

## Build

| # | Item | State | Evidence |
|---|---|---|---|
| B1 | `agent_comms/executors/sheila_ops.py`: status / bridge / message / run_adw operations + JSONL ledger, Decorator over Pi | done | `agent_comms/tests/test_sheila_ops.py` (16) |
| B2 | factory wiring (`sheila_operations`, dispatcher + replies into `default_builders`) | done | same suite, `test_the_card_builds_operations_in_front_of_pi` |
| B3 | BATI machine lane hosts Sheila as a PostOfficeAgent in workspace `sheila` | done (offline) | `agent_bridge_worker/tests/test_sheila_lane.py` |
| B4 | `adws/adw_sheila.py` + supervisor check `sheila` on BATI | done (offline) | ran on R46 2026-11-26T16:35Z: honestly `degraded`, "Rosemary46 (not BATI)" |
| B5 | Gateway: agent `sheila`, protected, pinned to BATI's worker token, read-only `sheila-bridge` client `k_8438df5d9ce7e513251e5d15` | done 2026-11-26T16:37Z | backup `~/agent_bridge_backups/bridge.php.20261126T163637Z.pre-sheila`; token 0600 at BATI `~/.config/sheila/bridge_read_token`; `/agents` 200 lists sheila; submit with it → 403 |
| B6 | Deploy table entry `agent_blocks_bati` | done (offline) | deployment tests 12 passed |
| B7 | Deploy to BATI through `adw_deploy.py` | done | releases adw `cdedb390` (16fdb7e/8ca64cf) and `2092efb5` (9ee9ff6), health check passing. SQMC: **not needed** -- its lanes never build Sheila (`Machine.lane_agents`) and the registry card change reaches SQMC's checkout on its next routine deploy |
| B8 | Gateway `RequestWatch`: only a request opened within 6 h adopts an undeclared task | done 2026-11-26T16:57Z | d8c1510; 196/196 gateway tests on the host; backup `~/agent_bridge_backups/20261126T165736Z_adopt_window` |
| B9 | A failed operation is not retried by the post office (`retryable: False`) | done | 9ee9ff6; live triple execution of `t_a2302bd175a614cb887efdb0` before, one execution of `t_cc8601a4a063e676af4820be` after |
| B10 | Readable ledger headline; named `User-Agent` (gateway 406 on urllib) | done | 3e794a4, 8ca64cf |

## Acceptance (section 13) -- see `SHEILA_ACCEPTANCE.json` for the machine-readable record

Results are in `SHEILA_ACCEPTANCE.json` and summarised in `SHEILA.md`.

## Still open

| Item | State | Why / whose |
|---|---|---|
| RSI pilot (section 15) | **open** | `adws/agent_improvement/profiles.py` has no Sheila `TargetProfile`; a cycle needs one plus a judge. Not started rather than faked |
| Dashboard readiness (section 17, test 19) | **partial** | the Requests panel shows the mission and Sheila's tasks from the gateway; nothing shows her readiness or BATI's lane. SQMC's `~/letta-code` has uncommitted changes that are not mine, so the dashboard was not edited |
| Close `rq_2efbd…` / `rq_e7f95…` | **blocked** | ChatGPT (OAuth) or EG |
