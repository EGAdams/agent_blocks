"""DAD's supervisor runs on the Codex CLI (Dad's ChatGPT login) instead of the Claude CLI. Offline: a fake `codex`
records how it was called; the guard is the real one, run as the hook would run it."""
import json
import os
import stat
import subprocess
import sys
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from machine_supervisor.adapters.codex_agent import DISABLED, CodexCli, hook_command, hook_setting
from machine_supervisor.adapters.mailer import ChainMailer, NoConnectorMailer
from machine_supervisor.adapters.usage_limit import ClaudeUsageLimit, CodexUsageLimit
from machine_supervisor.guards import bash_guard
from machine_supervisor.models import Machine, Mail, Policy

GUARD = Path(bash_guard.__file__)

FAKE = r'''#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
last = argv[argv.index("-o") + 1]
record = {"argv": argv, "stdin": sys.stdin.read(),
          "env": {k: v for k, v in os.environ.items() if k.startswith("MACHINE_SUPERVISOR_") or k.startswith("ANTHROPIC")}}
here = os.path.dirname(os.path.abspath(sys.argv[0]))
open(os.path.join(here, "record.json"), "w").write(json.dumps(record))
mode = open(os.path.join(here, "mode")).read().strip() if os.path.exists(os.path.join(here, "mode")) else "ok"
if mode == "limit":
    sys.stderr.write("ERROR: You've hit your usage limit. Upgrade to Plus to continue using Codex, or try again at 3:50 PM.\n")
    sys.exit(1)
if mode == "silent":
    sys.exit(0)
open(last, "w").write('Looked.\n{"diagnosis": "unit was dead", "actions": ["restarted x"], "believes_fixed": true, "needs": ""}\n')
'''


def dad(tmp: Path, **change) -> Machine:
    fake = tmp / "codex"
    fake.write_text(FAKE)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return Machine(**{"name": "DAD", "hostname": "dadscomputer", "ssh": {"host": "10.0.0.9"}, "python": sys.executable,
                      "repo": "/repo", "cli": "codex", "codex": str(fake), **change})


@pytest.fixture
def fake(tmp_path):
    return tmp_path


def recorded(tmp: Path) -> dict:
    return json.loads((tmp / "record.json").read_text())


def test_a_machine_names_the_path_of_the_cli_it_runs_on(tmp_path):
    assert dad(tmp_path).cli == "codex"
    with pytest.raises(ValueError):
        dad(tmp_path, codex="")
    with pytest.raises(ValueError):                      # the default is claude, which then needs its path
        Machine(name="X", hostname="x", ssh={"host": "h"}, python="/py", repo="/r")
    assert Machine(name="X", hostname="x", ssh={"host": "h"}, python="/py", repo="/r", claude="/c").cli == "claude"


def test_codex_runs_with_every_extra_tool_off_and_the_guard_on_every_tool(fake):
    machine = dad(fake)
    report = CodexCli(machine, Policy(), GUARD).report("todo", "look at X", "RULES", incident="i-1", look_only=True)
    assert report.reached and report.believes_fixed and report.actions == ["restarted x"] and report.cost_usd is None
    seen = recorded(fake)
    argv = seen["argv"]
    for flag in ("exec", "--ephemeral", "--ignore-user-config", "--dangerously-bypass-hook-trust", "-"):
        assert flag in argv
    disabled = {argv[i + 1] for i, a in enumerate(argv) if a == "--disable"}
    assert disabled == set(DISABLED) and {"apps", "multi_agent", "unified_exec_tty", "computer_use"} <= disabled
    assert 'web_search="disabled"' in argv and 'approval_policy="never"' in argv
    hooks = tomllib.loads(next(a for a in argv if a.startswith("hooks.PreToolUse=")))["hooks"]["PreToolUse"]
    assert hooks[0]["matcher"] == "*" and hooks[0]["hooks"][0]["command"] == hook_command(machine, GUARD)
    assert seen["stdin"].index("RULES") < seen["stdin"].index("look at X")        # no system-prompt flag in Codex
    assert seen["env"]["MACHINE_SUPERVISOR_LOOK_ONLY"] == "1" and seen["env"]["MACHINE_SUPERVISOR_INCIDENT"] == "i-1"
    assert "ANTHROPIC_API_KEY" not in seen["env"]


def test_a_usage_limit_is_an_error_the_todo_worker_can_wait_on(fake):
    (fake / "mode").write_text("limit")
    report = CodexCli(dad(fake), Policy(), GUARD).report("todo", "x", "r")
    assert not report.reached and "usage limit" in report.error
    assert CodexUsageLimit(timezone.utc).until(report.error, datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)) \
        == datetime(2026, 11, 27, 15, 55, tzinfo=timezone.utc)
    (fake / "mode").write_text("silent")
    silent = CodexCli(dad(fake), Policy(), GUARD).report("todo", "x", "r")
    assert not silent.reached and "did not answer" in silent.error


@pytest.mark.parametrize("said, expect", [
    ("You've hit your usage limit. Try again at 3:50 PM.", datetime(2026, 11, 27, 20, 55, tzinfo=timezone.utc)),
    ("You've hit your usage limit. Visit https://chatgpt.com/settings/usage to purchase more credits or try again at "
     "Nov 29 9:05 AM.", datetime(2026, 11, 29, 14, 10, tzinfo=timezone.utc)),
    ("You've hit your usage limit. Try again at 10:00 AM.", datetime(2026, 11, 28, 15, 5, tzinfo=timezone.utc)),
    ("You've hit your usage limit for GPT-6. try again in 2 hours 30 minutes",
     datetime(2026, 11, 27, 18, 35, tzinfo=timezone.utc)),
    ("You've hit your usage limit. Try again later.", datetime(2026, 11, 27, 17, 0, tzinfo=timezone.utc)),
])
def test_the_codex_limit_message_gives_the_time_to_try_again(said, expect):
    now = datetime(2026, 11, 27, 16, 0, tzinfo=timezone.utc)                          # 11:00 in New York
    assert CodexUsageLimit(ZoneInfo("America/New_York")).until(said, now) == expect


def test_other_codex_errors_are_not_a_usage_limit():
    now = datetime(2026, 11, 27, 16, 0, tzinfo=timezone.utc)
    for said in ("bwrap: loopback: Failed RTM_NEWADDR", "codex exited 1", "Selected model is at capacity.", ""):
        assert CodexUsageLimit().until(said, now) is None
    assert ClaudeUsageLimit().until("You've hit your 5-hour limit · resets 3:50pm (America/New_York)", now)


def hook(payload: str, command: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["sh", "-c", command], input=payload, capture_output=True, text=True,
                          env={**os.environ, **(env or {})})


@pytest.mark.parametrize("tool", ["apply_patch", "collaborationspawn_agent", "write_stdin",
                                  "mcp__codex_apps__sites_deploy", "web__run", "", None])
def test_the_guard_denies_every_tool_that_is_not_the_shell(tmp_path, tool):
    ran = hook(json.dumps({"tool_name": tool, "tool_input": {"command": "*** Begin Patch"}}),
               hook_command(dad(tmp_path), GUARD))
    assert ran.returncode == 0 and json.loads(ran.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_the_hook_as_codex_runs_it_allows_the_shell_and_fails_closed_with_a_reason(tmp_path):
    command = hook_command(dad(tmp_path), GUARD)
    assert hook(json.dumps({"tool_name": "Bash", "tool_input": {"command": "uptime"}}), command).stdout == ""
    denied = hook(json.dumps({"tool_name": "Bash", "tool_input": {"command": "rm -rf ~/x"}}), command)
    assert "recursive delete" in denied.stdout
    broken = hook("not json", command)                   # Codex blocks on exit 2 only when stderr gives a reason
    assert broken.returncode == 2 and "blocked" in broken.stderr
    missing = hook("{}", hook_command(dad(tmp_path, python="/nonexistent/python"), GUARD))
    assert missing.returncode == 2 and missing.stderr.strip()


def test_the_hook_setting_is_one_toml_value_even_with_quotes_in_paths(tmp_path):
    machine = dad(tmp_path, python='/odd "dir"/python')
    parsed = tomllib.loads(hook_setting(machine, GUARD))
    assert parsed["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == hook_command(machine, GUARD)


def test_a_codex_machine_holds_its_mail_instead_of_claiming_it_was_sent(tmp_path):
    held = []

    class Outbox:
        def hold(self, mail):
            held.append(mail)

    receipt = ChainMailer([NoConnectorMailer(dad(tmp_path))], Outbox()).send(Mail(subject="s", body="b"))
    assert not receipt.sent and receipt.via == "outbox" and "no Gmail connector" in receipt.detail and len(held) == 1


def test_the_kit_puts_codex_behind_dads_supervisor(tmp_path, monkeypatch):
    from machine_supervisor import kit
    from machine_supervisor.adapters.codex_agent import CodexCli as Codex
    table = json.loads((Path(kit.__file__).parent / "machines.json").read_text())
    monkeypatch.setenv("MACHINE_SUPERVISOR_ROOT", str(tmp_path / "root"))
    monkeypatch.setenv("MACHINE_SUPERVISOR_SETTINGS", str(tmp_path / "settings.json"))
    built = kit.Kit("DAD")
    assert built.catalog.me().cli == "codex" and isinstance(built.gmail, NoConnectorMailer)
    assert isinstance(built.todo_worker._agent._cli, Codex) and isinstance(built.todo_worker._limit, CodexUsageLimit)
    assert all(m["name"] == "DAD" or m.get("cli", "claude") == "claude" for m in table["machines"])
