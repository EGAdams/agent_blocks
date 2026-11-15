"""The mails EG gets, as plain text. Nothing here sends anything."""
from __future__ import annotations

from ..models import Attempt, Check, Incident, Machine, Mail


def _what_failed(incident: Incident) -> str:
    return "\n".join(f"  - {f.check}: {f.detail}" for f in incident.failures)


def _attempt(attempt: Attempt) -> str:
    r = attempt.report
    if not r.reached:
        return f"{attempt.rung}. {attempt.machine}'s supervisor could not be woken: {r.error}"
    lines = [f"{attempt.rung}. {attempt.machine}'s supervisor ({r.role})",
             f"   Found: {r.diagnosis or '(nothing reported)'}"]
    lines += [f"   Did: {action}" for action in r.actions] or ["   Did: nothing"]
    if r.needs:
        lines.append(f"   Says it needs: {r.needs}")
    if r.error:
        lines.append(f"   Error: {r.error}")
    lines.append("   Checks afterwards: " + ("all passing" if attempt.fixed else
                 "; ".join(f"{c.check} still failing ({c.detail})" for c in attempt.recheck if not c.ok)))
    return "\n".join(lines)


def _tag(incident: Incident) -> str:
    return "[DRILL] " if incident.drill else ""


def fixed(incident: Incident) -> Mail:
    names = ", ".join(f.check for f in incident.failures)
    if not incident.attempts:
        body = (f"On {incident.machine}, these checks failed:\n{_what_failed(incident)}\n\n"
                "By the time the supervisor woke and looked again, they were passing. Nothing was changed.\n")
        return Mail(subject=f"{_tag(incident)}[Machine Supervisor] {incident.machine}: {names} recovered by itself",
                    body=body + f"\nIncident {incident.id}")
    who = incident.attempts[-1].machine
    body = (f"On {incident.machine}, these checks failed:\n{_what_failed(incident)}\n\n"
            f"What was done:\n\n" + "\n\n".join(_attempt(a) for a in incident.attempts) +
            f"\n\nThe checks pass now. Fixed by {who}'s supervisor.\n\nIncident {incident.id}")
    return Mail(subject=f"{_tag(incident)}[Machine Supervisor] {incident.machine}: {names} failed and was fixed", body=body)


def recovered(machine: Machine, checks: list[str]) -> Mail:
    return Mail(subject=f"[Machine Supervisor] {machine.name}: {', '.join(checks)} is passing again",
                body=f"On {machine.name}, {', '.join(checks)} had been left unresolved and is passing again now.\n"
                     "The supervisors did not fix it; it came back by itself or somebody fixed it by hand.")


def wake_limit(machine: Machine, checks: list[str], limit: int, hours: float) -> Mail:
    return Mail(subject=f"HIGH PRIORITY [Machine Supervisor] {machine.name}: {', '.join(checks)} keeps failing",
                body=f"On {machine.name}, {', '.join(checks)} is failing, and the supervisor has already been woken "
                     f"{limit} times in the last 24 hours. It was not woken again. These checks are now left alone "
                     f"for {hours:g} hours, or until they pass.\n\nSomething is flapping and needs a person.")


def unresolved(incident: Incident, machine: Machine, checks: list[Check], machines: list[Machine]) -> Mail:
    names = ", ".join(f.check for f in incident.failures)
    tried = "\n\n".join(_attempt(a) for a in incident.attempts)
    reach = "\n".join(f"  - {m.name} ({m.hostname}): ssh -p {m.ssh.port} {m.ssh.user}@{m.ssh.host}" for m in machines)
    about = "\n".join(f"  - {c.name} ({c.kind} {c.target or ' '.join(c.argv)}): {c.about}" for c in checks)
    notes = "\n".join(f"  - {note}" for note in machine.notes)
    prompt = (f"You are taking over an unresolved incident on {machine.name} ({machine.hostname}).\n\n"
              f"Failing checks, as seen from {machine.name}:\n{_what_failed(incident)}\n\n"
              f"What each check is:\n{about}\n\nAbout {machine.name}:\n{notes}\n\n"
              f"How to reach the machines (Tailscale, key-only SSH):\n{reach}\n\n"
              f"Three supervisor agents already tried, in this order. Do not repeat what they did; start from "
              f"what they found:\n\n{tried}\n\n"
              f"Find the cause, fix it, and prove it: the fix counts only when the failing checks pass. "
              f"Re-run them with:\n  ssh -p {machine.ssh.port} {machine.ssh.user}@{machine.ssh.host} "
              f"{machine.python} {machine.repo}/adws/adw_machine_watch.py --dry-run\n\n"
              f"Do not commit, push, reset or delete anything, do not restart dashboard-server.service unless the "
              f"dashboard check is failing, and ask EG before rebooting Windows or shutting WSL down.\n"
              f"The full record is {machine.name}:~/.local/share/machine_supervisor/incidents/{incident.id}.json")
    body = (f"On {incident.machine}, these checks are failing and none of the supervisors could fix them:\n"
            f"{_what_failed(incident)}\n\nWhat was tried:\n\n{tried}\n\n"
            "PROMPT FOR A MORE CAPABLE AGENT (copy everything below this line)\n"
            "----------------------------------------------------------------\n" + prompt +
            f"\n----------------------------------------------------------------\n\nIncident {incident.id}")
    return Mail(subject=f"{_tag(incident)}HIGH PRIORITY [Machine Supervisor] {incident.machine}: {names} NOT fixed",
                body=body)
