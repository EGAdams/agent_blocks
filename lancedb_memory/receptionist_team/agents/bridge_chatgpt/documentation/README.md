# bridge_chatgpt

Toyota's Groq specialist for the Bridge agent `chatgpt-relay`. The worker side is
`agent_bridge_worker/chatgpt_relay_agent.py`; each task becomes one run of the relay ADW
(`adws/adw_chatgpt_relay.py`), with its own ADW id, lifecycle log and object trace.

This is the **same** Bridge agent, worker adapter and ADW that ChatGPT drives over MCP. Toyota gains no
operation of her own, and there is no second path into that browser -- only a second caller. See
`adws/chatgpt_relay/` for the operations and `~/.claude/skills/chatgpt-browser-relay/SKILL.md` for the
rules that window imposes.

## The operations

`ask` is the one to use: it checks the message, remembers the thread as it stands, types, sends, waits for
the reply to stop changing, and reports it. The others exist because when an ask fails the next move depends
on which half failed -- the message never arrived, or it arrived and nothing came back. `instances` names the
sessions; `health` and `ready` say whether one can be typed into; `read` shows the thread; `type` and `send`
are the two halves of a send, for when you want to put words in the box and stop.

A reply that was still being written when the wait ran out is reported as unfinished, never as the answer.
Half a sentence read as a decision is the one mistake this route could make that nobody would catch.

## What code decides, never the model

- The operation's lane: a read is `inspect`, a change is `implement`, so a change can never be sent as a read.
- `confirm` is **never** set from here, so `new_chat` is always refused: it abandons the thread on screen,
  and EG does that himself in the window.
- A password, PIN, one-time code, CVV or anything else matching the credential pattern is refused before it
  is typed. That account keeps its history, so a secret sent once is sent for good.
- There is no `quit` and no `close`, anywhere in any table. That Chrome is signed in to EG's account and
  closing it costs the login.
- `instance`, `origin` and `hops` are what stop two sessions that can message each other from ping-ponging:
  a forwarded message never goes back where it came from, and the hop count has a ceiling.

Every `implement` operation is read back and waits for a spoken yes, the same gate `bridge_writer` and
`bridge_browser` use.

See `receptionist_team/bridge_client/README.md` (Bridge specialists) and `relay_plans.py`.
