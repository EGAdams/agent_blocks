# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

`ClaudeAgentAdapter` implements the `ConversationAgent` port
(`ports.py`) by wrapping `claude-agent-sdk`'s `query()` call -- the same SDK
probed by `catherine_agent_sdk/basic_agent.py` and already adapted once by
`lancedb_memory/agent_memory/adapters/claude_model.py`. It is the sibling
adapter role to `voice_communication/letta_agent_adapter/letta_agent_adapter.ts`
(`ILettaAgentAdapter`), documented as the `Claude Adapter` tab in
`spa_documentation/`.

`ConversationAgent.submit(text, generation)` mirrors `IConversationAgent`
(`voice_communication/conversation_agent/conversation_agent.ts`) on the
Python/claude-agent-sdk side of the boundary. This directory is
self-contained -- `models.py` and `ports.py` are local copies of the shapes
`voice_communication` already defines, not imports across them, because
`agent_blocks` has no root package (see the root `CLAUDE.md`'s "Running"
section: every project directory runs standalone).

**Important, and easy to misread:** `ClaudeAgentAdapter` is not a channel
into any particular running Claude Code conversation, including the one
that wrote this code. Every `submit()` call can open a fresh
`claude-agent-sdk` session with no memory of, and no way back into, any
other session. It is reproducible -- any other instance can construct one
the same way -- *because* it depends on nothing but the recipe in
`adapter.py`.

## Layout

```text
claude_agent_adapter/
  models.py   AgentEvent / AgentEventKind (Pydantic)
  ports.py    ConversationAgent (Protocol) -- adapter.py depends on this and
              .models, and nothing else
  adapter.py  ClaudeAgentAdapter -- the only file that imports claude_agent_sdk
  tests/      offline tests; fakes.py builds real claude_agent_sdk message
              objects so isinstance checks pass with no network call
```

## Running

```bash
# claude-agent-sdk is not installed in the default interpreter; use a venv
# that has it, e.g. the one lancedb_memory/agent_memory already depends on:
cd claude_agent_adapter
../lancedb_memory/.venv/bin/python tests/test_adapter.py
```

## The rule that matters when touching this

`adapter.py` may import `.models`, `.ports`, and `claude_agent_sdk`, and
nothing else. That is the DIP boundary: it is the one file in this
directory allowed to name the concrete SDK.

## Auth requirement

Same as `catherine_agent_sdk/basic_agent.py` and
`lancedb_memory/agent_memory/adapters/claude_model.py`: `ANTHROPIC_API_KEY`
and `ANTHROPIC_BASE_URL` must stay **unset** so the SDK uses the local
Claude Code subscription auth rather than direct-API billing.
`ResultMessage.is_error` is where auth/model/execution failures surface --
they do not raise -- so `submit()` checks it before yielding a terminal
event and raises `AgentCallFailed` instead of silently forwarding a failed
call as if it succeeded.

## Style

Matches `catherine_agent_sdk/basic_agent.py` and `lancedb_memory/agent_memory`:
every non-empty `)`, `]`, or `}` gets a space before it (`str )`, `[ 0.0 ]`),
and every non-empty `(`, `[`, or `{` gets a space after it, next to the
innermost non-bracket content. Adjacent brackets stack tight with no space
between them (`] )` becomes `])`). Empty pairs stay tight (`()`, `[]`, `{}`).
