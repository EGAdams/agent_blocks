"""The supervisor agent: the Claude CLI on this machine's subscription login, asleep until one of these wakes it.

It gets Bash, Read, Grep and Glob, and every Bash command passes guards/bash_guard.py first (a PreToolUse hook
given with --settings; `|| exit 2` makes a guard that cannot run block the command rather than pass it).
What it says it fixed is never believed: the escalation runs the checks again.
"""
from __future__ import annotations

import json
from pathlib import Path

from agent_comms.models import Envelope, TaskStatus

from ..errors import Undelivered
from ..interfaces import IBriefWriter, IMessenger, IRepairAgent
from ..models import AgentReport, Incident, Machine, Policy
from .runtime import child_env, run

TOOLS = "Bash,Read,Grep,Glob"


def find_report(text: str) -> dict | None:
    """The last JSON object in `text` that has a diagnosis."""
    decoder, at = json.JSONDecoder(), len(text)
    while (at := text.rfind("{", 0, at)) != -1:
        try:
            found, _ = decoder.raw_decode(text[at:])
        except ValueError:
            continue
        if isinstance(found, dict) and "diagnosis" in found:
            return found
    return None


class ClaudeCli:
    def __init__(self, machine: Machine, policy: Policy, guard: Path) -> None:
        self._machine, self._policy, self._guard = machine, policy, guard

    def ask(self, prompt: str, rules: str, timeout: int, incident: str = "", look_only: bool = False,
            no_talk: bool = False) -> tuple[str, float | None, str]:
        """(what it said, cost, error). Never raises."""
        hook = f"{self._machine.python} {self._guard} || exit 2"
        settings = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": hook}]}]}}
        extra = {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "MACHINE_SUPERVISOR_INCIDENT": incident,
                 "MACHINE_SUPERVISOR_LOOK_ONLY": "1" if look_only else "", "MACHINE_SUPERVISOR_NO_TALK": "1" if no_talk else ""}
        ran = run([self._machine.claude, "-p", "--model", self._policy.model, "--output-format", "json",
                   "--tools", TOOLS, "--allowedTools", TOOLS, "--append-system-prompt", rules,
                   "--no-session-persistence", "--settings", json.dumps(settings)],
                  timeout, stdin=prompt, env=child_env(extra, windows=True), cwd=str(Path.home()))
        try:
            said = json.loads(ran.out)
            text, cost = str(said.get("result") or ""), said.get("total_cost_usd")
            return text, cost, (text[:500] or "the agent reported an error") if said.get("is_error") else ""
        except (ValueError, AttributeError):
            return "", None, f"the agent did not answer: {ran.said}"

    def report(self, role: str, prompt: str, rules: str, incident: str = "", look_only: bool = False) -> AgentReport:
        text, cost, error = self.ask(prompt, rules, self._policy.agent_timeout, incident, look_only)
        if error and not text:
            return AgentReport(machine=self._machine.name, role=role, reached=False, error=error)
        found = find_report(text) or {}
        actions = found.get("actions")
        return AgentReport(machine=self._machine.name, role=role, cost_usd=cost, error=error,
                           diagnosis=str(found.get("diagnosis") or text)[:4000],
                           actions=[str(a)[:500] for a in actions] if isinstance(actions, list) else [],
                           believes_fixed=found.get("believes_fixed") is True, needs=str(found.get("needs") or "")[:2000])


class LocalRepairAgent:
    def __init__(self, cli: ClaudeCli, briefs: IBriefWriter) -> None:
        self._cli, self._briefs = cli, briefs

    def attempt(self, incident: Incident, role: str) -> AgentReport:
        return self._cli.report(role, self._briefs.repair(incident, role), self._briefs.rules(), incident.id,
                                look_only=incident.drill)


class LocalAnswerer:
    """Looks, answers, changes nothing, and may not ask anybody else: a question stops here."""
    def __init__(self, cli: ClaudeCli, briefs: IBriefWriter, policy: Policy) -> None:
        self._cli, self._briefs, self._policy = cli, briefs, policy

    def answer(self, sender: str, question: str, incident: Incident | None) -> str:
        text, _, error = self._cli.ask(self._briefs.answer(sender, question, incident), self._briefs.rules(),
                                       self._policy.ask_timeout, incident.id if incident else "", look_only=True,
                                       no_talk=True)
        if error:
            raise RuntimeError(error)
        return text[:6000]


class LocalTodoAgent:
    def __init__(self, cli: ClaudeCli, briefs: IBriefWriter) -> None:
        self._cli, self._briefs = cli, briefs

    def work(self, envelope: Envelope) -> AgentReport:
        return self._cli.report("todo", self._briefs.todo(envelope), self._briefs.rules())


class PeerRepairAgent:
    """Another machine's supervisor, woken by a `help` handoff. Its reply is that agent's own report."""
    def __init__(self, machine: Machine, messenger: IMessenger, policy: Policy) -> None:
        self._machine, self._messenger, self._policy = machine, messenger, policy

    def attempt(self, incident: Incident, role: str) -> AgentReport:
        names = ", ".join(f.check for f in incident.failures)
        try:
            reply = self._messenger.send(self._machine, "help", f"Help with {incident.machine}: {names}",
                                         {"incident": incident.model_dump(mode="json")},
                                         self._policy.agent_timeout + 120, priority=20)
        except Undelivered as error:
            return AgentReport(machine=self._machine.name, role=role, reached=False, error=str(error))
        if reply.outcome is not TaskStatus.SUCCEEDED:
            return AgentReport(machine=self._machine.name, role=role, error=reply.error or "the helper failed")
        try:
            return AgentReport.model_validate_json(reply.result or "").model_copy(update={"role": role})
        except ValueError:
            return AgentReport(machine=self._machine.name, role=role, diagnosis=(reply.result or "")[:4000])


class AgentDirectory:
    def __init__(self, me: Machine, local: IRepairAgent, messenger: IMessenger, policy: Policy) -> None:
        self._me, self._local, self._messenger, self._policy = me, local, messenger, policy

    def on(self, machine: Machine) -> IRepairAgent:
        return self._local if machine.name == self._me.name else PeerRepairAgent(machine, self._messenger, self._policy)
