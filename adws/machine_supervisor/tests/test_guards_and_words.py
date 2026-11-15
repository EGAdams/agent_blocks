import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from machine_supervisor.adapters.agent import find_report
from machine_supervisor.adapters.briefs import FileBriefWriter
from machine_supervisor.adapters.mailer import SEND_TOOL, body_digest, confirmed
from machine_supervisor.guards import bash_guard, mail_guard
from machine_supervisor.models import Incident

from .fakes import FakeClock, FakeProbe, catalog

GUARDS = Path(bash_guard.__file__).parent


@pytest.mark.parametrize("command", [
    "rm -rf ~/letta-code", "sudo rm -fr /var/lib/x", "git -C ~/agent_blocks push", "cd repo && git reset --hard",
    "wsl.exe --shutdown", "sudo reboot", "powershell.exe Restart-Computer -Force", "cat ~/.claude/.credentials.json",
    "mysql -e 'DROP TABLE expenses'", "docker compose down", "docker volume rm pg", "crontab -r", "crontab new.txt",
    "systemctl --user disable dashboard-server", "sudo apt-get install -y foo", "pip install x", "sudo tailscale down",
    "dd if=/dev/zero of=/dev/sda", "ssh host 'git commit -am x'", "echo x | sudo tee /etc/hosts"])
def test_lasting_damage_is_denied(command):
    assert bash_guard.verdict(command, look_only=False)


@pytest.mark.parametrize("command", [
    "systemctl --user restart dashboard-proxy.service", "journalctl --user -u dashboard-server -n 80", "git status",
    "git -C ~/agent_blocks log --oneline -5", "docker ps", "docker restart letta-server", "crontab -l", "uptime",
    "ssh -o BatchMode=yes -p 2222 adamsl@100.102.209.100 systemctl --user is-active dashboard-server",
    "sudo -n systemctl restart tailscaled", "curl -s -o /dev/null -w '%{http_code}' http://localhost:8765/",
    "kill 1234", "tailscale status"])
def test_ordinary_repairs_are_allowed(command):
    assert bash_guard.verdict(command, look_only=False) is None


@pytest.mark.parametrize("command", ["systemctl --user restart x", "kill 1", "docker restart letta-server",
                                     "echo hi > /tmp/x", "sudo systemctl stop tailscaled"])
def test_look_only_also_denies_changes(command):
    assert bash_guard.verdict(command, look_only=True)
    assert bash_guard.verdict("systemctl --user status x 2>&1 | tail -5", look_only=True) is None


def hook(guard, payload, env=None):
    return subprocess.run([sys.executable, str(GUARDS / guard)], input=payload, capture_output=True, text=True,
                          env={**os.environ, **(env or {})})


def test_the_bash_hook_denies_in_the_hook_format_and_fails_closed():
    ran = hook("bash_guard.py", json.dumps({"tool_name": "Bash", "tool_input": {"command": "git push"}}))
    assert json.loads(ran.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny" and ran.returncode == 0
    assert hook("bash_guard.py", json.dumps({"tool_name": "Bash", "tool_input": {"command": "uptime"}})).stdout == ""
    assert hook("bash_guard.py", "not json").returncode == 2


def test_the_mail_guard_passes_only_the_one_exact_mail():
    body = "Line one.\n\n  Line two."
    env = {"to": "eg@example.org", "subject": "S", "digest": body_digest(body)}
    good = {"to": ["eg@example.org"], "subject": "S", "body": "Line one.\n\nLine two.\n"}        # whitespace may differ
    assert mail_guard.verdict(SEND_TOOL, good, **env) is None
    assert mail_guard.verdict("ToolSearch", {}, **env) is None
    for bad in ({**good, "to": ["eg@example.org", "x@evil.test"]}, {**good, "to": ["x@evil.test"]},
                {**good, "cc": ["x@evil.test"]}, {**good, "bcc": ["x@evil.test"]}, {**good, "subject": "Other"},
                {**good, "body": "Line one. And a secret."}, {**good, "attachments": [{"path": "/etc/passwd"}]},
                {**good, "htmlBody": "<b>x</b>"}):
        assert mail_guard.verdict(SEND_TOOL, bad, **env)
    assert mail_guard.verdict("Bash", {"command": "uptime"}, **env)
    assert mail_guard.verdict("mcp__claude_ai_Gmail__search_threads", {}, **env)
    assert mail_guard.verdict(SEND_TOOL, good, to="", subject="S", digest="")
    assert body_digest(body) == hashlib.sha256(b"Line one. Line two.").hexdigest()


def test_the_mail_hook_fails_closed_without_its_environment():
    payload = json.dumps({"tool_name": SEND_TOOL, "tool_input": {"to": ["a@b.c"], "subject": "S", "body": "B"}})
    clean = {"MACHINE_SUPERVISOR_MAIL_TO": "", "MACHINE_SUPERVISOR_MAIL_SUBJECT": "", "MACHINE_SUPERVISOR_MAIL_SHA": ""}
    assert "deny" in hook("mail_guard.py", payload, clean).stdout
    assert hook("mail_guard.py", "{{{").returncode == 2


def test_sent_means_the_connector_said_so():
    use = {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": SEND_TOOL}]}}
    result = lambda error: {"type": "user", "message": {"content": [                              # noqa: E731
        {"type": "tool_result", "tool_use_id": "t1", "is_error": error, "content": "x"}]}}
    lines = lambda *events: "\n".join(json.dumps(e) for e in events)                             # noqa: E731
    assert confirmed(lines(use, result(False))) == (True, "")
    assert confirmed(lines(use, result(True)))[0] is False
    assert confirmed(lines(use))[0] is False
    assert confirmed(json.dumps({"type": "result", "result": "sent"}))[0] is False               # a claim is not proof
    assert confirmed("garbage\n")[0] is False


def test_the_agents_report_is_the_last_json_object_with_a_diagnosis():
    text = 'I ran {"a": 1} then.\n{"diagnosis": "unit was dead", "actions": ["restarted"], "believes_fixed": true, "needs": ""}'
    assert find_report(text)["actions"] == ["restarted"]
    assert find_report("no json here") is None and find_report('{"other": 1}') is None


def test_every_brief_fills_every_blank(tmp_path):
    from machine_supervisor import envelopes
    cat = catalog(tmp_path, "SQMC")
    briefs = FileBriefWriter(cat, GUARDS.parent / "prompts")
    origin = cat.get("BATI")
    incident = Incident(id="i-1", machine="BATI", opened_at=FakeClock().now(), checks=origin.checks,
                        failures=[FakeProbe({"web"}).look(origin.checks[0])])
    todo = envelopes.request("chatgpt-bridge", "supervisor-sqmc", "todo", "look at X", {}, 50, FakeClock().now())
    texts = [briefs.rules(), briefs.repair(incident, "first"), briefs.repair(incident, "helper"),
             briefs.answer("supervisor-bati", "is it up?", incident), briefs.todo(todo),
             briefs.repair(incident.model_copy(update={"drill": True}), "helper")]
    for text in texts:
        assert "$" not in text, text
    assert "ssh -o BatchMode=yes -p 2222 adamsl@10.0.0.1" in texts[2] and "THIS IS A DRILL" in texts[5]
    assert "adw_machine_talk.py" in texts[0] and "may be switched off" in texts[0] and "look at X" in texts[4]
