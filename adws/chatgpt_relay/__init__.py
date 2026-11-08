"""ChatGPT Relay: one message into a standing, signed-in ChatGPT browser session, and its reply back.

Read `DESIGN.md` before changing anything here -- it names each pattern and the file it lives in.

The short version: `catalog.relay_operations()` is the table, each entry is a Command over the
`plane.RelayPlane` Facade, `relay.ask` is a Composite of seven traced steps, and the three callers
(Claude Code, Toyota, ChatGPT) all reach the same table through `adws/adw_chatgpt_relay.py`.

Two rules that are not negotiable:

- **There is no quit.** Quitting that Chrome discards a signed-in profile a person then restores by hand,
  so the capability is absent from `IRelayTransport` rather than present and guarded.
- **No credential is ever typed into the page.** `guards.CredentialGuard` refuses it before the transport
  is reached, because the message lands in a real account's permanent history and cannot be unsent.

Nothing is imported here on purpose: `agent_comms.operations.base` lives in `lancedb_memory/`, which the
ADW harness and the tests' conftest put on the path themselves. A package import that reached for it
would make merely naming this package depend on that path already being set.
"""
