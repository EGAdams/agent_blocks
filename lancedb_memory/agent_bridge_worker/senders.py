"""The Star Trek Bridge senders a worker-side agent will take a task from.

One definition, because four agents used to spell the set out separately. The
gateway binds each sender to an authenticated token id (`client_scopes` in
`bridge.php`); this set only says which of those bound senders the workers on
this side accept. Adding a sender here gives it no new operation: every agent
still applies its own operation, mode, workspace and approval checks.

- `chatgpt-bridge`: ChatGPT, through the MCP door
- `toyota-bridge`:  Toyota, the finance receptionist
- `sydney-bridge`:  Sydney, the receptionist for code, agents, browser and ADWs
"""

APPROVED_BRIDGE_SENDERS: frozenset[ str ] = frozenset({ "chatgpt-bridge", "toyota-bridge", "sydney-bridge" })


def approved_senders_text() -> str:
    return ", ".join( sorted( APPROVED_BRIDGE_SENDERS ))
