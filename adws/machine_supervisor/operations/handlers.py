"""IHandoffHandler, one class per kind of handoff a peer's supervisor may send. kit.py registers them by name."""
from __future__ import annotations

from agent_comms.models import Envelope

from ..interfaces import IAnswerer, IIncidentStore, IMailer, IRepairAgent, IToast
from ..models import Alert, Incident, Mail


class AlertHandler:
    """Show a toast on this machine's Windows desktop."""
    def __init__(self, toast: IToast) -> None:
        self._toast = toast

    def handle(self, envelope: Envelope) -> str:
        return self._toast.show(Alert(title=str(envelope.context.get("title", "Machine Supervisor"))[:120],
                                      message=str(envelope.context.get("message", envelope.text))[:400]))


class HelpHandler:
    """Wake this machine's supervisor to help with another machine's incident. The reply is its AgentReport.
    It does not take the supervisor lock: a helper may run beside this machine's own first responder."""
    def __init__(self, agent: IRepairAgent, incidents: IIncidentStore) -> None:
        self._agent, self._incidents = agent, incidents

    def handle(self, envelope: Envelope) -> str:
        incident = Incident.model_validate(envelope.context.get("incident"))
        self._incidents.save(incident)                   # so a question this helper asks can carry it along
        return self._agent.attempt(incident, "helper").model_dump_json()


class AskHandler:
    """Answer one question from a peer's supervisor. The answerer looks and never changes anything."""
    def __init__(self, answerer: IAnswerer) -> None:
        self._answerer = answerer

    def handle(self, envelope: Envelope) -> str:
        carried = envelope.context.get("incident")
        incident = Incident.model_validate(carried) if isinstance(carried, dict) else None
        return self._answerer.answer(envelope.sender, envelope.text, incident)


class MailHandler:
    """Send a peer's mail through this machine's own mailer. Never passed on again from here."""
    def __init__(self, mailer: IMailer) -> None:
        self._mailer = mailer

    def handle(self, envelope: Envelope) -> str:
        receipt = self._mailer.send(Mail(subject=str(envelope.context.get("subject", "")),
                                         body=str(envelope.context.get("body", envelope.text))))
        if not receipt.sent:
            raise RuntimeError(receipt.detail or "not sent")
        return f"sent via {receipt.via}"
