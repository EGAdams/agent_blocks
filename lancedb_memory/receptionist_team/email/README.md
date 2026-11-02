# Yolanda: email for the team

Yolanda is a `ReceptionistProfile` on Toyota's existing conversation engine,
with her own session, archive, voice, email-only roster and loopback port 8771.
Agents send her tasks through `agent_comms`; she operates the external mailbox.
The post office itself is not an email provider.

Gmail uses **the same connected Claude Gmail connector** as interactive Claude
Code. `EmailConnectorRunner` launches the installed Claude CLI through the Agent
SDK with native ToolSearch discovery, the existing local subscription login,
and no project settings. It never copies OAuth tokens or stores credentials in
this repository. Do not set `tools=["ToolSearch"]`: that disables native deferred
connector discovery. The pre-tool hook restricts execution to the selected
connector and actions requested by the current task. Mail contents are data.

`provider.json` selects the provider name, Claude connector key, and tool model.
Gmail defaults to `claude_ai_Gmail`, model `sonnet`. An alternate selection can
be loaded with `YOLANDA_EMAIL_CONFIG`; switching to Outlook changes that
configuration and the connected Claude account, not every calling agent.
`YOLANDA_CLAUDE_BIN` optionally chooses the installed CLI. No custom Gmail MCP
server or extra Google OAuth application is required for this configured login.

From the agent_blocks root:

```bash
lancedb_memory/.venv/bin/python adws/adw_op_delegate_task.py \
  --sender sydney --text 'yolanda: Check the Gmail connection without reading messages.'
```

A draft stays unsent. A send, reply, forward or mailbox change requires that
instruction in the current task. Yolanda asks for missing recipients/content
rather than guessing. A delivery receipt is not a sent-email confirmation.
Mutation requests use a non-idempotent post-office message so failed sends are
not blindly retried. Yolanda's conversation is not peer-recalled by the finance
receptionists; their task replies contain only what she explicitly returns.

Start: `.venv/bin/python -m receptionist_team.service --agent yolanda`
from `lancedb_memory/`. The deployed user unit is `yolanda-receptionist`.
Dashboard: `/?agent=agent-yolanda&view=input-options` through `/api/yolanda-turn`.
Tests: `python -m receptionist_team.tests.test_yolanda_profile`.

Task results have an explicit status: completed, needs_information, or failed.
Clarifications are returned verbatim regardless of final punctuation. A Gmail
search confirms a read, not the creation of a draft or a send.

For an explicit request to attach the latest finance report, request_context
loads the published report from ADW Generated's local manifest. The application
injects its exact base64 bytes into the Gmail attachment input; the model gets
only the filename and source context. No unrestricted file tools are enabled.
User-confirmed recipient aliases live outside the repository at
`~/.config/agent_blocks/yolanda_contacts.json` (`{"aliases": {"Mom": "..."}}`),
overridable with `YOLANDA_CONTACTS_FILE`. Unknown aliases require clarification.
