# unset ANTHROPIC_API_KEY
# unset ANTHROPIC_BASE_URL
"""ToolUsingAgent -- runs one mazda_team AgentRole via the Claude Agent SDK.

Unlike agent_memory/adapters/claude_model.py (allowed_tools=[], max_turns=1,
text-only chat), these roles write local files, so tools are enabled and
permission prompts are bypassed --
this runs headless, there is no human at the keyboard to approve each call.
Same auth requirement as every other adapter in agent_blocks: ANTHROPIC_API_KEY
and ANTHROPIC_BASE_URL must stay UNSET so the SDK uses local subscription auth.
"""
from dataclasses import dataclass

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)

from ..specialist_roles import AgentRole


class ToolAgentError( RuntimeError ):
    pass


_DETAIL_KEYS_BY_TOOL = {
    "Bash": ( "command", "description" ),
    "Read": ( "file_path", ),
    "Write": ( "file_path", ),
    "Edit": ( "file_path", ),
    "Glob": ( "pattern", "path" ),
    "Grep": ( "pattern", "path" ),
}
_MAX_DETAIL_LEN = 160


def _describe_tool_call( block: ToolUseBlock ) -> str:
    """One line of what the tool call is actually doing, e.g. the shell
    command for Bash or the path for Read/Write -- not just the tool name."""
    keys = _DETAIL_KEYS_BY_TOOL.get( block.name, tuple( block.input.keys())[ :1 ] )
    parts = []
    for key in keys:
        value = block.input.get( key )
        if not value:
            continue
        text = str( value ).replace( "\n", " ⏎ " ).strip()
        if len( text ) > _MAX_DETAIL_LEN:
            text = text[ : _MAX_DETAIL_LEN ] + "…"
        parts.append( f"{key}={text}" if key != keys[ 0 ] else text )
    return "; ".join( parts ) if parts else "(no args)"


@dataclass
class RoleResult:
    role_name: str
    output_path: str
    final_text: str
    tool_calls: int
    cost_usd: float | None
    is_error: bool


async def run_role( role: AgentRole, cwd: str, model: str, echo: bool = True ) -> RoleResult:
    options = ClaudeAgentOptions(
        model=model,
        system_prompt=role.system_prompt,
        allowed_tools=role.allowed_tools,
        max_turns=role.max_turns,
        cwd=cwd,
        permission_mode="bypassPermissions",
        setting_sources=[],
        # See agent_memory/adapters/claude_model.py's comment on this same
        # line: forces local subscription auth per-subprocess regardless of
        # an ambient ANTHROPIC_API_KEY in the caller's shell (one was found
        # live in ~/.bashrc on this machine and silently switches billing to
        # a metered key until its balance runs dry).
        env={ "ANTHROPIC_API_KEY": "", "ANTHROPIC_BASE_URL": "" },
    )

    tool_calls = 0
    final_text_chunks: list[ str ] = []
    final_result: ResultMessage | None = None

    async for message in query( prompt=role.task_prompt, options=options ):
        if isinstance( message, AssistantMessage ):
            for block in message.content:
                if isinstance( block, TextBlock ):
                    final_text_chunks.append( block.text )
                elif isinstance( block, ToolUseBlock ):
                    tool_calls += 1
                    if echo:
                        print(
                            f"[{role.name}] tool call #{tool_calls}: {block.name} "
                            f"-- {_describe_tool_call( block )}"
                        )
        elif isinstance( message, ResultMessage ):
            final_result = message

    if final_result is None:
        raise ToolAgentError( f"[{role.name}] no ResultMessage from the Agent SDK." )

    text = "".join( final_text_chunks ).strip() or ( final_result.result or "" ).strip()

    return RoleResult(
        role_name=role.name,
        output_path=role.output_path,
        final_text=text,
        tool_calls=tool_calls,
        cost_usd=final_result.total_cost_usd,
        is_error=final_result.is_error,
    )
