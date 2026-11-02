"""Reach the same authenticated Claude connector the operator already uses."""
import asyncio
import os
import shutil
from pathlib import Path

from agent_memory.models import AgentReply
from ..email.config import load_settings
from ..email.tool_policy import EmailToolPolicy
from ..email.outcome import EmailTaskOutcome, outcome_text, tool_action
from ..email.request_context import EmailRequestContext


class EmailConnectorRunner:
    def __init__(self, settings_loader=load_settings, query_fn=None, context_factory=EmailRequestContext) -> None:
        self._settings_loader = settings_loader
        self._query = query_fn
        self._context_factory = context_factory

    async def run(self, role: object, agent_name: str, model: str, extra: str = "") -> AgentReply:
        try:
            async with asyncio.timeout(600):
                return await self._run(role, extra)
        except TimeoutError:
            return AgentReply(text="The email connector timed out. I can't confirm whether an action finished; check its status before sending again.")
        except Exception as error:
            # Never expose provider auth payloads or credentials in a spoken error.
            return AgentReply(text=f"Yolanda couldn't complete the email request ({type(error).__name__}). No successful action was confirmed.")

    async def _run(self, role: object, request: str) -> AgentReply:
        from claude_agent_sdk import (
            AssistantMessage, ClaudeAgentOptions, HookMatcher, ResultMessage,
            ToolResultBlock, ToolUseBlock, UserMessage, query,
        )
        config = self._settings_loader()
        policy = EmailToolPolicy(config.tool_prefix, request)
        prepared = self._context_factory(request)

        async def before_tool(data, tool_use_id, context):
            decision = await policy.hook(data, tool_use_id, context)
            if decision:
                print(f"[yolanda-email] denied tool {data.get('tool_name', '')}", flush=True)
                return decision
            updated = prepared.tool_input(data.get("tool_name", ""), data.get("tool_input", {}), config.tool_prefix)
            if updated is not None:
                return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                        "permissionDecision": "allow", "updatedInput": updated}}
            return {}

        options = ClaudeAgentOptions(
            cli_path=os.environ.get("YOLANDA_CLAUDE_BIN") or shutil.which("claude"),
            model=config.model, system_prompt=role.system_prompt +
                f"\nConfigured provider: {config.provider}. Only use tools with prefix {config.tool_prefix}.\n{prepared.notes}",
            # Leave native MCP discovery enabled. ToolSearch resolves the existing
            # Claude connector lazily; no OAuth tokens are copied or exported.
            allowed_tools=["ToolSearch", "StructuredOutput", config.tool_prefix + "*"],
            output_format={"type": "json_schema", "schema": EmailTaskOutcome.model_json_schema()},
            max_turns=role.max_turns, cwd=str(Path(__file__).resolve().parents[3]),
            setting_sources=[], permission_mode="bypassPermissions",
            hooks={"PreToolUse": [HookMatcher(hooks=[before_tool])]},
            env={"ANTHROPIC_API_KEY": "", "ANTHROPIC_BASE_URL": "",
                 "CLAUDE_CODE_OAUTH_TOKEN": "", "ENABLE_CLAUDEAI_MCP_SERVERS": "true",
                 "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"},
        )
        calls, successful, result = {}, set(), None
        async for message in (self._query or query)(prompt=request, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, ToolUseBlock):
                        calls[block.id] = block.name
                        print(f"[yolanda-email] tool {block.name}", flush=True)
            elif isinstance(message, UserMessage) and isinstance(message.content, list):
                for block in message.content:
                    if isinstance(block, ToolResultBlock) and not block.is_error and \
                       calls.get(block.tool_use_id, "").startswith(config.tool_prefix):
                        successful.add(tool_action(calls[block.tool_use_id], config.tool_prefix))
            elif isinstance(message, ResultMessage):
                result = message
        if result is None:
            return AgentReply(text="The email connector returned no final result. I couldn't confirm an email action.")
        if not result.structured_output:
            return AgentReply(text="No email action was confirmed. The email runner returned no structured task outcome.", cost_usd=result.total_cost_usd)
        outcome = EmailTaskOutcome.model_validate(result.structured_output)
        print(f"[yolanda-email] status={outcome.status} action={outcome.action} confirmed={sorted(successful)}", flush=True)
        return AgentReply(text=outcome_text(outcome, successful), cost_usd=result.total_cost_usd)
