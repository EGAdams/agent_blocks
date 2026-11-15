#!/usr/bin/env python3
"""PreToolUse hook for the supervisor agent's Bash tool. Standard library only; run by file path.

It reads the hook's JSON on stdin and denies a command that matches the table below. With
MACHINE_SUPERVISOR_LOOK_ONLY=1 (a drill, or answering a peer's question) it also denies anything that changes
a service. Any failure of the guard itself exits 2, which blocks the command.

This is a deny table, not a sandbox: it stops the known ways to do lasting damage, and the rules in the
agent's brief cover the rest.
"""
import json
import os
import re
import sys

ALWAYS = [
    (r"\brm\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*[rR]", "recursive delete"),
    (r"\bgit\b[^|;&]*\b(push|reset|commit|clean|rebase|checkout\s+--|restore|stash)\b", "git history or working tree change"),
    (r"\bwsl(\.exe)?\b[^|;&]*--(shutdown|terminate|unregister)", "shutting WSL down"),
    (r"\b(shutdown|reboot|poweroff|halt|init\s+[06])\b|Restart-Computer|Stop-Computer", "rebooting or powering off"),
    (r"\.credentials\.json|\.ssh/id_|\.ssh/authorized_keys|/etc/shadow|\.env\b|bridge_token|\.config/startrek", "credential files"),
    (r"\b(mkfs|fdisk|parted|wipefs)\b|\bdd\s+[^|;&]*\bof=", "disk tools"),
    (r"\b(DROP|TRUNCATE)\s+(TABLE|DATABASE|SCHEMA)\b|\bDELETE\s+FROM\b", "destructive SQL"),
    (r"\bdocker\b[^|;&]*\b(rm|rmi|prune|down)\b|\bdocker\s+volume\b", "removing containers, images or volumes"),
    (r"\bcrontab\s+(-[a-z]*\s+)*-[a-z]*r|\bcrontab\s+[^-\s]", "replacing or removing the crontab"),
    (r"\bsystemctl\b[^|;&]*\b(disable|mask|daemon-reexec|isolate|poweroff|reboot|halt)\b", "disabling a unit"),
    (r"\b(apt|apt-get|dpkg|snap|pip|pip3|npm|uv\s+pip)\s+[^|;&]*\b(install|remove|purge|upgrade|uninstall)\b", "installing or removing packages"),
    (r"\b(chmod|chown)\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*R", "recursive permission change"),
    (r">\s*/etc/|\btee\s+(-a\s+)?/etc/", "writing under /etc"),
    (r"\btailscale\s+(down|logout|up\b)", "changing Tailscale's state"),
]
LOOK_ONLY = [
    (r"\bsystemctl\b[^|;&]*\b(start|stop|restart|reload|try-restart|kill|enable|reset-failed|edit|set-property)\b", "changing a unit"),
    (r"\b(kill|pkill|killall)\b", "ending a process"),
    (r"\bdocker\b[^|;&]*\b(start|stop|restart|kill|exec|run|compose\s+up)\b", "changing a container"),
    (r"(^|[;&|]\s*)(sudo\s+)?(mv|cp|tee|truncate|touch|mkdir|ln|sed\s+-i)\b|>>?\s*[^&\s]", "writing files"),
]


def verdict(command: str, look_only: bool) -> str | None:
    """Why this command is denied, or None."""
    for pattern, why in ALWAYS + (LOOK_ONLY if look_only else []):
        if re.search(pattern, command, re.IGNORECASE):
            return why
    return None


def main() -> int:
    try:
        data = json.load(sys.stdin)
        if data.get("tool_name") != "Bash":
            return 0
        command = str(data.get("tool_input", {}).get("command", ""))
        look_only = os.environ.get("MACHINE_SUPERVISOR_LOOK_ONLY") == "1"
        why = verdict(command, look_only)
        if why:
            mode = " (you are in look-only mode)" if look_only else ""
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": f"Machine Supervisor guard: {why} is not allowed{mode}. "
                                            "Do not try another way round; put it in `needs` instead."}}))
        return 0
    except Exception as error:                               # fail closed
        print(f"bash_guard failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
