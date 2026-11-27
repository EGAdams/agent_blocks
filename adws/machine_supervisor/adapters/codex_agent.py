"""IAgentCli over the Codex CLI on this machine's ChatGPT login: for a machine whose `cli` is codex (DAD).

It does what ClaudeCli does, with the guard in front of every tool, not only the shell:

- Every feature that adds a tool beyond the shell is switched off (ChatGPT apps, plugins, browser and computer
  use, images, goals, web search, sub-agents). What is left: exec_command, apply_patch, write_stdin, a clock
  and empty MCP resource listings.
- One PreToolUse hook with the catch-all matcher runs guards/bash_guard.py, which denies any tool but the shell
  (apply_patch, collaboration.*, anything a later Codex adds) and the deny table on shell commands. Codex passes
  a shell command as tool_name "Bash", the Claude wire format.
- TTY execution is off (unified_exec, unified_exec_tty). With it on, write_stdin types into an open shell and
  that typing never reaches the hook (seen on DAD, 2026-11-27); with it off, stdin is closed.
- Codex blocks a call on a hook that exits 2 only when the hook also writes a reason to stderr. With no reason it
  logs "Failed" and runs the command. So a guard that cannot run says so, which keeps the guard fail-closed.
- The user's ~/.codex/config.toml is ignored, the run leaves no session behind, and the hook is trusted for this
  one run only (--dangerously-bypass-hook-trust), so no hook trust is written into Codex's config.

There is no OS sandbox: Codex's bubblewrap sandbox fails on DAD (unprivileged user namespaces are restricted
and bubblewrap is not installed; fixing it needs sudo). That is the same footing as the Claude supervisors: a
deny table, not a sandbox.

Codex has no system-prompt flag, so the rules go first in the prompt. It reports no cost.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from ..models import AgentReport, Machine, Policy
from .agent import to_report
from .runtime import child_env, run

DISABLED = ("apps", "plugins", "remote_plugin", "browser_use", "browser_use_external", "computer_use",
            "image_generation", "goals", "tool_suggest", "skill_mcp_dependency_install", "skill_search", "view_image",
            "sleep_tool", "in_app_browser", "in_app_chat", "in_app_local_automation", "multi_agent",
            "unified_exec", "unified_exec_tty")
BLOCKED = "Machine Supervisor guard could not run, so this tool call is blocked."


def hook_command(machine: Machine, guard: Path) -> str:
    return f"{machine.python} {guard} || {{ echo '{BLOCKED}' >&2; exit 2; }}"


def hook_setting(machine: Machine, guard: Path) -> str:
    """The `-c` override that installs the guard; a JSON string is also a TOML basic string."""
    return f'hooks.PreToolUse=[{{matcher="*",hooks=[{{type="command",command={json.dumps(hook_command(machine, guard))}}}]}}]'


class CodexCli:
    def __init__(self, machine: Machine, policy: Policy, guard: Path) -> None:
        self._machine, self._policy, self._guard = machine, policy, guard

    def argv(self, last: Path) -> list[str]:
        disable = [arg for feature in DISABLED for arg in ("--disable", feature)]
        return [self._machine.codex, "exec", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config",
                "--dangerously-bypass-hook-trust", "-s", "danger-full-access", "-c", 'approval_policy="never"',
                "-c", 'web_search="disabled"', *disable, "-c", hook_setting(self._machine, self._guard),
                "-C", str(Path.home()), "-o", str(last), "-"]

    def ask(self, prompt: str, rules: str, timeout: int, incident: str = "", look_only: bool = False,
            no_talk: bool = False) -> tuple[str, float | None, str]:
        """(what it said, None, error). Never raises."""
        extra = {"MACHINE_SUPERVISOR_INCIDENT": incident, "MACHINE_SUPERVISOR_LOOK_ONLY": "1" if look_only else "",
                 "MACHINE_SUPERVISOR_NO_TALK": "1" if no_talk else ""}
        with tempfile.TemporaryDirectory(prefix="ms_codex_") as scratch:
            last = Path(scratch) / "last_message.txt"
            ran = run(self.argv(last), timeout, stdin=f"{rules}\n\n---\n\n{prompt}", env=child_env(extra),
                      cwd=str(Path.home()))
            try:
                text = last.read_text(encoding="utf-8").strip()
            except OSError:
                text = ""
        if ran.code != 0:
            said = (ran.err or ran.out).strip()
            return text, None, said[-800:] or f"codex exited {ran.code}"
        return text, None, "" if text else f"the agent did not answer: {(ran.err or ran.out).strip()[-500:]}"

    def report(self, role: str, prompt: str, rules: str, incident: str = "", look_only: bool = False) -> AgentReport:
        return to_report(self._machine.name, role, *self.ask(prompt, rules, self._policy.agent_timeout, incident,
                                                             look_only))
