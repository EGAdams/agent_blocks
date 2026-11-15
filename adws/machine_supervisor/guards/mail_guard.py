#!/usr/bin/env python3
"""PreToolUse hook for the mail-carrying model. Standard library only; run by file path.

Exactly one kind of call passes: the Gmail connector's send_message, to the one configured recipient, with the
expected subject and the expected body (compared by SHA-256 after collapsing whitespace) and nothing else --
no cc, no bcc, no attachment, no draft. ToolSearch passes because it is how the connector's tool gets loaded.
Every other tool is denied. Any failure of the guard itself exits 2, which blocks the call.
"""
import hashlib
import json
import os
import sys

SEND_TOOL = "mcp__claude_ai_Gmail__send_message"
ALLOWED_KEYS = {"to", "subject", "body", "includeSignature"}


def verdict(tool: str, args: dict, to: str, subject: str, digest: str) -> str | None:
    """Why this call is denied, or None."""
    if tool == "ToolSearch":
        return None
    if tool != SEND_TOOL:
        return f"only {SEND_TOOL} may be called"
    if not to or not digest:
        return "no recipient or body was configured for this run"
    extra = sorted(key for key, value in args.items() if key not in ALLOWED_KEYS and value not in (None, "", [], {}))
    if extra:
        return f"fields not allowed: {', '.join(extra)}"
    if args.get("to") != [to]:
        return "the recipient must be exactly the configured address"
    if args.get("subject") != subject:
        return "the subject must be exactly the one you were given"
    body = args.get("body")
    if not isinstance(body, str) or hashlib.sha256(" ".join(body.split()).encode("utf-8")).hexdigest() != digest:
        return "the body must be exactly the text between the markers, unchanged"
    return None


def main() -> int:
    try:
        data = json.load(sys.stdin)
        why = verdict(str(data.get("tool_name", "")), data.get("tool_input") or {},
                      os.environ.get("MACHINE_SUPERVISOR_MAIL_TO", ""), os.environ.get("MACHINE_SUPERVISOR_MAIL_SUBJECT", ""),
                      os.environ.get("MACHINE_SUPERVISOR_MAIL_SHA", ""))
        if why:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                     "permissionDecisionReason": f"Machine Supervisor mail guard: {why}."}}))
        return 0
    except Exception as error:                               # fail closed
        print(f"mail_guard failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
