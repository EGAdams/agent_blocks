# bridge_bati_desktop

Toyota's (and Sydney's) Groq specialist for the Bridge agent `bati-desktop`. The worker side is
`agent_bridge_worker/bati_desktop_agent.py` on BATI's own machine-lane worker; each task becomes one run of
`adws/adw_bati_chrome.py` (design in `adws/bati_desktop/DESIGN.md`), with its own ADW id and audit lines.

This is the **same** Bridge agent, worker adapter and ADW that ChatGPT drives over MCP (`bati_*` tools).
Toyota gains no browser path of her own -- only a second caller.

## The operations

Reads (`inspect`, no yes needed): `status`, `read`, `screenshot`.
Changes (`implement`, read back, sent only after a yes): `open`, `show_view`, `plan_for_today`.

## What code decides, never the model

- The operation's lane (`bridge_client/bati_plans.py`).
- The machine: always `BATI`. The gateway's `worker_pins` offers `bati-desktop` to BATI's token alone, the worker
  refuses another machine, and the ADW checks WSL's hostname and Windows' COMPUTERNAME. Nothing is rerouted.
- `url` only on the dashboard origin, `view` only `plan-for-today`; refused here in words first.
- There is no type, fill, close or quit; asking for one is answered in words and never sent.
- `adapters/bati_desktop_route.py` forces a message naming BATI and Chrome/desktop/dashboard here, ahead of
  `bridge_browser` (SQMC's Chrome) and Scala's filing route.
