"""IBriefWriter: the words an agent is woken with. The prompts are files in prompts/; this fills them in."""
from __future__ import annotations

from pathlib import Path
from string import Template

from agent_comms.models import Envelope

from ..interfaces import IMachineCatalog
from ..models import Incident, Machine


def _ssh(machine: Machine) -> str:
    return f"ssh -o BatchMode=yes -p {machine.ssh.port} {machine.ssh.user}@{machine.ssh.host}"


def _notes(machine: Machine) -> str:
    return "\n".join(f"  - {note}" for note in machine.notes) or "  (no notes)"


class FileBriefWriter:
    def __init__(self, catalog: IMachineCatalog, prompts: Path) -> None:
        self._catalog, self._prompts = catalog, prompts

    def _fill(self, name: str, **values: str) -> str:
        return Template((self._prompts / name).read_text(encoding="utf-8")).safe_substitute(values)

    def rules(self) -> str:
        me = self._catalog.me()
        machines = "\n".join(
            f"  - {m.name} ({m.hostname}): " + ("this machine" if m.name == me.name else _ssh(m)) +
            ("" if m.always_on else "  -- may be switched off; that is normal")
            for m in [me, *self._catalog.peers()])
        talk = f'{me.python} {me.repo}/adws/adw_machine_talk.py --to <BATI|SQMC|R46> --message "your question"'
        return self._fill("rules.md", machine=me.name, hostname=me.hostname, machines=machines, talk=talk)

    def repair(self, incident: Incident, role: str) -> str:
        me, origin = self._catalog.me(), self._catalog.get(incident.machine)
        dry = f"{origin.python} {origin.repo}/adws/adw_machine_watch.py --dry-run"
        earlier = "\n".join(
            f"  {a.rung}. {a.machine}: " + (f"found: {a.report.diagnosis} | did: {'; '.join(a.report.actions) or 'nothing'}"
                                           f" | needs: {a.report.needs or '-'}" if a.report.reached
                                           else f"could not be woken ({a.report.error})")
            for a in incident.attempts) or "  (nothing yet)"
        text = self._fill(
            "first.md" if role == "first" else "helper.md", machine=me.name, origin=origin.name, incident=incident.id,
            failures="\n".join(f"  - {f.check}: {f.detail}" for f in incident.failures),
            checks="\n".join(f"  - {c.name} ({c.kind} {c.target or ' '.join(c.argv)}): {c.about}" for c in incident.checks),
            notes=_notes(me), origin_notes=_notes(origin), origin_ssh=_ssh(origin), earlier=earlier,
            recheck=dry if origin.name == me.name else f"{_ssh(origin)} {dry}")
        return text + (self._fill("drill.md") if incident.drill else "")

    def answer(self, sender: str, question: str, incident: Incident | None) -> str:
        about = ""
        if incident is not None:
            about = (f"The problem is incident {incident.id} on {incident.machine}: " +
                     "; ".join(f"{f.check} ({f.detail})" for f in incident.failures) + "\n")
        return self._fill("answer.md", sender=sender, machine=self._catalog.me().name, question=question, about=about)

    def todo(self, envelope: Envelope) -> str:
        resumed = ""
        if envelope.context.get("interrupted_resumes"):
            resumed = ("\nA previous attempt at this todo was interrupted before it reported (its worker stopped at "
                       f"{envelope.context.get('interrupted_at', 'an unknown time')}). Check what that attempt already "
                       "did before doing anything, do not repeat a change that is already made, and say in the report "
                       "what you found it had done.\n")
        return self._fill("todo.md", sender=envelope.sender, text=envelope.text, id=envelope.id,
                          notes=_notes(self._catalog.me()), resumed=resumed)
