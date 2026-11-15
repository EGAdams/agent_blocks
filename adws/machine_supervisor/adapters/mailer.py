"""IMailer, three ways, tried in order by ChainMailer: this machine's Gmail connector, a peer's, the outbox.

The connector is the one the Claude CLI's subscription login already has (Yolanda uses the same); no token is
read, copied or exported. The model in the path only carries the text: guards/mail_guard.py lets exactly one
call through -- the configured recipient, this subject, this body -- and a mail counts as sent only when the
connector's own tool result says so.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agent_comms.models import TaskStatus

from ..errors import Undelivered
from ..interfaces import IMachineCatalog, IMailer, IMessenger, IOutbox
from ..models import Machine, Mail, MailReceipt, Policy
from .runtime import child_env, run

SEND_TOOL = "mcp__claude_ai_Gmail__send_message"


def body_digest(body: str) -> str:
    return hashlib.sha256(" ".join(body.split()).encode("utf-8")).hexdigest()


def recipient(settings: Path) -> str:
    try:
        return str(json.loads(settings.read_text(encoding="utf-8")).get("email_to", "")).strip()
    except (OSError, ValueError, AttributeError):
        return ""


def confirmed(stream: str) -> tuple[bool, str]:
    """Did the connector's tool result for a send come back without an error? (yes/no, why not)."""
    sends, why = set(), "the model never called the Gmail connector"
    for line in stream.splitlines():
        try:
            event = json.loads(line)
            content = event.get("message", {}).get("content", [])
        except (ValueError, AttributeError):
            continue
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if event.get("type") == "assistant" and block.get("type") == "tool_use" and block.get("name") == SEND_TOOL:
                sends.add(block.get("id"))
            if event.get("type") == "user" and block.get("type") == "tool_result" and block.get("tool_use_id") in sends:
                if not block.get("is_error"):
                    return True, ""
                why = f"the connector refused: {json.dumps(block.get('content'))[:300]}"
    return False, why


class GmailConnectorMailer:
    def __init__(self, machine: Machine, policy: Policy, settings: Path, guard: Path) -> None:
        self._machine, self._policy, self._settings, self._guard = machine, policy, settings, guard

    def send(self, mail: Mail) -> MailReceipt:
        via, to = f"{self._machine.name} gmail", recipient(self._settings)
        if not to:
            return MailReceipt(sent=False, via=via, detail=f"no email_to in {self._settings}")
        hook = f"{self._machine.python} {self._guard} || exit 2"
        settings = {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": hook}]}]}}
        env = child_env({"CLAUDE_CODE_OAUTH_TOKEN": "", "ENABLE_CLAUDEAI_MCP_SERVERS": "true",
                         "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "MACHINE_SUPERVISOR_MAIL_TO": to,
                         "MACHINE_SUPERVISOR_MAIL_SUBJECT": mail.subject,
                         "MACHINE_SUPERVISOR_MAIL_SHA": body_digest(mail.body)})
        prompt = (f"Send one email with the tool {SEND_TOOL} (load it with ToolSearch \"select:{SEND_TOOL}\" if it "
                  f"is not loaded). Use exactly these arguments and no others:\n"
                  f"to: [\"{to}\"]\nsubject: {mail.subject}\nbody: everything between the two marker lines below, "
                  "copied character for character as plain text.\n"
                  "Call the tool once. Use no other tool. Then answer with the single word sent, or the error.\n"
                  f"=====BODY=====\n{mail.body}\n=====END=====\n")
        ran = run([self._machine.claude, "-p", "--model", self._policy.model, "--output-format", "stream-json",
                   "--verbose", "--allowedTools", f"ToolSearch,{SEND_TOOL}", "--no-session-persistence",
                   "--settings", json.dumps(settings)],
                  self._policy.mail_timeout, stdin=prompt, env=env, cwd=str(Path.home()))
        sent, why = confirmed(ran.out)
        return MailReceipt(sent=sent, via=via, detail="" if sent else (why if ran.out.strip() else ran.said))


class PeerMailer:
    """Hand the mail to a peer's supervisor ADW, which sends it through that machine's own connector."""
    def __init__(self, peer: Machine, messenger: IMessenger, policy: Policy) -> None:
        self._peer, self._messenger, self._policy = peer, messenger, policy

    def send(self, mail: Mail) -> MailReceipt:
        via = f"{self._peer.name} gmail"
        try:
            reply = self._messenger.send(self._peer, "mail", mail.subject, mail.model_dump(),
                                         self._policy.mail_timeout + 60, priority=20)
        except Undelivered as error:
            return MailReceipt(sent=False, via=via, detail=str(error))
        sent = reply.outcome is TaskStatus.SUCCEEDED
        return MailReceipt(sent=sent, via=via, detail="" if sent else (reply.error or "not sent"))


class ChainMailer:
    """The first mailer that sends wins. If none does, the mail is held for the next flush, never dropped."""
    def __init__(self, mailers: list[IMailer], outbox: IOutbox) -> None:
        self._mailers, self._outbox = mailers, outbox

    def send(self, mail: Mail) -> MailReceipt:
        receipt, tried = self.try_send(mail)
        if not receipt.sent:
            self._outbox.hold(mail)
            return MailReceipt(sent=False, via="outbox", detail="held for the next try; " + tried)
        return receipt

    def try_send(self, mail: Mail) -> tuple[MailReceipt, str]:
        tried = []
        for mailer in self._mailers:
            receipt = mailer.send(mail)
            if receipt.sent:
                return receipt, ""
            tried.append(f"{receipt.via}: {receipt.detail}")
        return MailReceipt(sent=False, detail="; ".join(tried)), "; ".join(tried)


def peer_mailers(catalog: IMachineCatalog, messenger: IMessenger) -> list[IMailer]:
    return [PeerMailer(peer, messenger, catalog.policy()) for peer in catalog.peers()]
