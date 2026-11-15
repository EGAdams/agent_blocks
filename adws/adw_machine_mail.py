#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic"]
# ///
"""ADW Machine Mail -- send one mail to the address in this machine's settings file, or retry held mail.

    adw_machine_mail.py --subject "..." --body "..."     through this machine's Gmail connector, else a peer's
    adw_machine_mail.py --flush                          try every held mail again (the watch starts this)

There is no --to: each machine sends only to the one recipient in ~/.config/machine_supervisor/settings.json.
A mail nobody could send is held in the outbox and retried, never dropped. "Sent" means the connector's own
tool result said so, not that a model said it did.
"""
import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from machine_supervisor.adw import finish, start                         # noqa: E402
from machine_supervisor.models import Mail                               # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Send one mail, or flush held mail.")
    parser.add_argument("--flush", action="store_true")
    parser.add_argument("--subject")
    parser.add_argument("--body")
    parser.add_argument("--json-out")
    args = parser.parse_args()
    if not args.flush and not (args.subject and args.body):
        parser.error("give --flush, or both --subject and --body")
    adw_id, kit = start("mail --flush" if args.flush else "mail")
    if not args.flush:
        receipt = kit.mailer.send(Mail(subject=args.subject, body=args.body))
        line = f"sent via {receipt.via}" if receipt.sent else f"not sent, held for retry: {receipt.detail}"
        kit.log.line(f"mail: {line}")
        return finish(args.json_out, receipt.sent, line, receipt.model_dump(), adw_id)
    with kit.lock.hold("mail") as mine:
        if not mine:
            return finish(args.json_out, True, "another flush is running", adw_id=adw_id)
        sent, pending = 0, kit.outbox.pending()
        for key, mail in pending:
            receipt, tried = kit.mailer.try_send(mail)
            if receipt.sent:
                kit.outbox.drop(key)
                sent += 1
                kit.log.line(f"held mail sent via {receipt.via}: {mail.subject}")
            else:
                kit.log.line(f"held mail still not sent ({tried}): {mail.subject}")
    return finish(args.json_out, sent == len(pending), f"{sent} of {len(pending)} held mails sent", adw_id=adw_id)


if __name__ == "__main__":
    sys.exit(main())
