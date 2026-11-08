# chatgpt_relay — design

The ChatGPT relay: one message into a standing, signed-in ChatGPT browser
session on another machine, and its reply back. Three callers reach it, and
they reach the *same* one — there is no second mechanism for any of them:

```
Claude Code  ──┐
               ├──►  adws/adw_chatgpt_relay.py  ──►  chatgpt_relay/  ──►  browser_server.py  ──►  Chrome
ChatGPT (MCP) ─┤        (the ADW: phases,            (this package:        (letta-code, on         (signed in,
Toyota        ─┘         lifecycle log, ADW id)       the operations)        desktop-shdbati)        persistent)
```

ChatGPT arrives through the gateway's `chatgpt_*` MCP tools
(`agent_bridge/gateway/src/RelayTools.php`) and Toyota through her
`bridge_chatgpt` route
(`lancedb_memory/receptionist_team/bridge_client/relay_plans.py`); both become
one Bridge task that `agent_bridge_worker/chatgpt_relay_agent.py` turns into
one run of the ADW. Read `~/.claude/skills/chatgpt-browser-relay/SKILL.md` for
how to drive it, this file for how it is built.

## What this is for

ChatGPT may want to send a message to **another instance of itself**. That is
why instances are plural and addressable, why `origin` and `hops` exist, and
why `instances` is a registry rather than a constant.

## The patterns, and where each one lives

| pattern | where | why it is that and not an `if` |
|---|---|---|
| **Facade** | `plane.RelayPlane` | One surface over transport, instances, guards, settling and observers. Every operation takes exactly one collaborator. |
| **Command** | each class in `operations.py` | One operation, one class, one `run`. A new one is a class plus a line in `catalog.py`. |
| **Composite** | `OperationSequence`, built in `catalog.relay_operations` | `relay.ask` *is* seven operations — ready, guard, snapshot, type, send, settle, answer — not a method that does seven things. Each step is traced on its own, so a failed ask names the step that failed. |
| **Template Method** | `RelayOperation.run` | The fixed shape every operation shares (resolve the instance, do the one thing, return an outcome); subclasses fill in the one thing. |
| **Strategy** | `ISettlePolicy` / `settling.py` | How you decide a reply is finished — text stopped changing, a new turn appeared, don't wait — is a choice, and `--settle` is the caller making it. |
| **Abstract Factory** | `plane.default_plane()` | The one place that names concretes. Tests build a plane with a fake transport and no network. |
| **Adapter + remote Proxy** | `transport.HttpRelayTransport` | `browser_server.py`'s HTTP is adapted to one `call()`; the object stands in for a Chrome on another machine. |
| **Decorator** | `RetryingTransport`, `ObservedTransport` | Retry and tracing wrap the transport without the operations knowing either exists. |
| **Chain of Responsibility** | `guards.GuardChain` | Each guard answers one question (empty, too long, a credential, too many hops, back to its origin); the first refusal wins. A new rule is a link, not a branch. |
| **Observer + Null Object** | `IRelayObserver` / `NoRelayObserver` | The ADW's lifecycle log is an observer. Nothing checks for `None`. |
| **Registry** | `instances.INSTANCES`, `catalog.PUBLIC` | A second ChatGPT session is a data entry. The operation table is read by the ADW, the worker and the gateway instead of each keeping its own list. |
| **Memento** | `models.ThreadSnapshot` | The thread as it stood *before* sending, which is the only way to tell the reply apart from what was already there. |

## Five rules, each enforced at every layer

Stated once here because they are repeated in the gateway, the worker and this
package on purpose — a caller who reaches one layer directly still meets them.

1. **There is no `quit` and no `close`.** Not in `catalog.PUBLIC`, not in the
   worker's `READS`/`WRITES`, not in `RelayTools::TOOLS`. That Chrome is signed
   in to EG's account and closing it costs the login, so the operation does not
   exist rather than being guarded. Tests on both sides assert the absence.
2. **A credential is refused before anything is typed.** That account keeps its
   history: a secret sent once is sent for good, and no later refusal can reach
   it. The guard runs before `type`, Toyota's plan refuses it before the task
   is built, and the gateway refuses it again.
3. **An unsettled reply is never reported as the answer.** `settled: false`
   means the reply was still being written when the deadline won. The worker
   makes `verified` false on it and the gateway's `next` line says in words not
   to read the text as the answer. Half a sentence read as a decision is the
   one mistake here that nobody would catch.
4. **`new_chat` needs an explicit `confirm`.** It abandons the thread on
   screen. Toyota never sets it, so from her desk it is always refused.
5. **Nothing is ever resent.** `type` and `send` are deliberately **not** in
   `RetryingTransport.RETRIED` (only `health`, `open`, `new_chat`,
   `read_thread` are): retrying a send that already arrived posts the message
   twice. When an ask fails past the send step the next move is `relay.read`.

## Two numbers that were measured, not chosen

- `transport.SLOW_TIMEOUT = 150.0` for `/type`, `/open`, `/new_chat` against
  `DEFAULT_TIMEOUT = 20.0` for everything else. `browser_server.py` types
  character by character; a 63-character message timed out at 20s on the first
  live run.
- `MAX_MESSAGE = 7000` in the worker and the gateway. `ClaimedTask.prompt` is
  capped at 8000 by pydantic, so a higher limit here could never be reached — a
  too-long message would have been rejected upstream as a malformed task
  instead of refused with a sentence about the message.

## Two error types, because the next move differs

`errors.py`: **`RelayUnreachable`** means nothing answered — the message was not
delivered and waiting may fix it. **`RelayRefused`** means the relay declined on
purpose — repeating gives the same answer. Collapsing them into one failure is
how a caller ends up retrying a refusal forever.

## One name, used twice

The Bridge agent and its workspace are both called `chatgpt-relay`.
`self_contained` is read as agent names in `agent_bridge_worker/service.py` and
as workspace names in `lanes.write_workspaces`, so an agent and its workspace
must share one spelling — otherwise a write is refused with a message about
workspaces that says nothing about the two names being different. That cost an
afternoon once; don't split them.

## Verified

`relay.ask` end to end against the live session, 7/7 phases, ADW id `5ea1a596`:
`bati says: RELAY OK`, `settled: true`. The gateway's and worker's own suites
pass offline (131 gateway checks, 14 worker tests, 101 for Toyota's route), and
the gateway files are **not** deployed to the host yet. Neither ChatGPT's nor
Toyota's path has had a live run — don't call either verified.
