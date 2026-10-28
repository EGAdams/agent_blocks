"""ClaudeSdkExecutor -- Sonnet or Opus as a worker, through the Claude Agent SDK.

This is how a build request actually gets built. It is an Adapter over the
same `finance_team.adapters.tool_agent.run_role` every delegated specialist
already runs through (dashboard_expert edits real code that way today), so
no provider code changed: auth stays local subscription OAuth, with that
module's `env=` backstop for ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL intact.

What the model may do is decided by the agent's card (`tools`) and the brief
for the kind of work (`briefs/<task_type>.md`) -- not by a dispatcher's
opinion of whether the request "is a question". The briefs carry the house
rules that do belong here: work in the named repository, run its tests, never
push, never run destructive git.

`run_role` is imported inside `execute` so importing this package never drags
in the SDK; tests inject `role_runner`.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ..models import Envelope, TaskResult

BRIEFS = Path( __file__ ).resolve().parents[ 1 ] / "briefs"

RoleRunner = Callable[ ..., Awaitable[ Any ]]


class ClaudeSdkExecutor:

    def __init__( self,
                  display_name: str,
                  model:        str,
                  tools:        list[ str ],
                  workdir:      str,
                  output_dir:   str,
                  max_turns:    int = 60,
                  role_runner:  RoleRunner | None = None ) -> None:
        self._display_name = display_name
        self._model        = model
        self._tools        = list( tools )
        self._workdir      = workdir
        self._output_dir   = output_dir
        self._max_turns    = max_turns
        self._role_runner  = role_runner

    @property
    def performer( self ) -> str:
        return f"claude_sdk:{ self._model }"

    async def execute( self, envelope: Envelope ) -> TaskResult:
        from finance_team.roles import AgentRole   # dataclass only; no SDK import

        workdir = str( envelope.context.get( "workdir" ) or self._workdir )
        output  = str( Path( self._output_dir ) / f"{ envelope.id }.md" )
        role = AgentRole(
            name          = f"{ envelope.recipient }_{ envelope.task_type }",
            output_path   = output,
            system_prompt = self._system_prompt( envelope ),
            task_prompt   = self._task_prompt( envelope, workdir, output ),
            allowed_tools = self._tools,
            max_turns     = self._max_turns,
        )
        result = await self._runner()( role, cwd=workdir, model=self._model, echo=False )
        return TaskResult( ok=not result.is_error, text=reply_line( result.final_text ),
                           performer=self.performer, cost_usd=result.cost_usd,
                           details={ "tool_calls": result.tool_calls, "output_path": output, "workdir": workdir })

    def _runner( self ) -> RoleRunner:
        if self._role_runner is None:
            from finance_team.adapters.tool_agent import run_role
            self._role_runner = run_role
        return self._role_runner

    def _system_prompt( self, envelope: Envelope ) -> str:
        return _brief( "worker_system" ).format( name=self._display_name, sender=envelope.sender )

    def _task_prompt( self, envelope: Envelope, workdir: str, output: str ) -> str:
        brief = _brief( envelope.task_type, fallback="ask" )
        return brief.format( request=envelope.text, sender=envelope.sender, workdir=workdir,
                             output=output, task_id=envelope.id, context=envelope.context or "{}" )


def reply_line( final_text: str ) -> str:
    """The worker's REPLY: line (briefs/worker_system.md), or all of its text.

    run_role joins every text block of the session, so without the marker the
    reply would carry the worker's running commentary along with its answer.
    """
    text = final_text.strip()
    marker = text.rfind( "REPLY:" )
    if marker >= 0:
        return text[ marker + len( "REPLY:" ): ].strip() or text
    return text or "(no reply text)"


def _brief( name: str, fallback: str | None = None ) -> str:
    path = BRIEFS / f"{ name }.md"
    if not path.exists() and fallback:
        path = BRIEFS / f"{ fallback }.md"
    return path.read_text( encoding="utf-8" )
