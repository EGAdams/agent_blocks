# bridge_cashy

The Groq specialist (on Toyota's and Sydney's rosters, via `roles.BRIDGE_ROLE_NAMES`) for the Bridge agent
`cashy-r46`: Cashy, the banking navigation agent, on R46 (Mom's computer). The worker side is R46's own worker
(`--lane machine`, `agent_bridge_worker/cashy_agent.py`); read `agent_bridge/docs/CASHY_R46.md` first.

This is the **same** Bridge agent, worker and Cashy ADWs that ChatGPT drives over MCP. The receptionist gains no
banking path of her own -- only another caller, on her own gateway identity.

## The operations

Reads (`inspect`, no yes needed): `status`, `read_page`, `list_options`.
Changes (`implement`, read back, sent only after a yes): `open_bank`, `logged_in`, `pause`, `resume`, `stop`.

## What code decides, never the model

- The operation's lane (`bridge_client/cashy_plans.py`), the machine (always `R46`), and the session
  (`r46-bridge` for Fifth Third, `r46-fnbo` for FNBO).
- Paying, transferring, passwords and approvals are answered in words and never sent. The worker sends the Bridge
  sender as Cashy's actor, never `user`, so no request from here can approve one of her consequential steps.
- `logged_in` is read back as "only say yes if you've signed in yourself".
- `adapters/cashy_route.py` forces this route when a message names Cashy with R46 (or `cashy-r46`), so the
  dashboard's own Cashy window ("open Cashy") is never hijacked and a request for R46 never lands there.
- The gateway lists `cashy-r46` as protected and pins it to R46's worker.
