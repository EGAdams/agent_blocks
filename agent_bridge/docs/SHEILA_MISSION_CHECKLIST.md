# Sheila mission checklist (EG, 2026-11-26, "GET SHEILA FULLY OPERATIONAL")

Persistent state for the mission. **accepted ≠ completed**: a row is `done` only with the evidence named beside it.
States: `done` (evidence) · `open` · `blocked` (why, whose) · `superseded` (by what). Times are UTC.

## Decisions recorded

| Item | Disposition | Why |
|---|---|---|
| SQMC `agent_blocks_dev`'s `adw_interface_sheila_demo.py`, its `sheila/README.md` and `test_sheila_demo_adw.py` | **superseded** -- not ported | Written when Pi was absent (capabilities build/ask/message, no Pi). Main 22eadbf already has the real executor; porting would make a second implementation. Left untouched in that checkout. |
| Supervisor todo `8d8d4aed` / Bridge task `t_a7ab6b7cc3a8fabb79e87dc3` / envelope `20261125T201929Z_8d8d4aed_from_chatgpt-bridge` | **stays failed (historical)** -- not replayed | Failed 2026-11-25T20:19:41Z on the Claude 5-hour usage limit; no Sheila run happened. Replaying would duplicate work this mission does directly. |
| Request `rq_2efbd0b987493fa1bf234914` (`sheila-finish-20261125`) | to be closed by a `request_update` naming this mission's acceptance task ids | its 24 adopted tasks are the history of yesterday's attempt |
| Request `rq_e7f95a093cc0deb6a74d886d` | to be marked a duplicate of `rq_2efbd…` | same goal, opened separately |
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
| B7 | Deploy to BATI through `adw_deploy.py` and to SQMC | open | |

## Acceptance (section 13) -- see `SHEILA_ACCEPTANCE.json` for the machine-readable record

All twenty: open until run live on BATI after B7.
