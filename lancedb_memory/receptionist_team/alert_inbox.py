"""IAlertInbox -- the failures a receptionist has to tell the user about.

A task she sent through the post office that failed raises an alert for her
(agent_comms/alerts.py). Her sidecar reads them here: `GET /alerts` for the
dashboard, which shows, speaks and notifies them the moment they land, and
the start of her next turn, which says them out loud first -- so a failure
reaches the user even when nobody has the dashboard open.

The Protocol and its null object only; the post office's adapter is
adapters/post_office_alerts.py, so building a receptionist never imports
agent_comms.
"""
from typing import Protocol


class IAlertInbox(Protocol):
    def pending(self) -> list[dict]:
        """Each alert as JSON-ready data with at least "id" and "spoken"."""
        ...

    def acknowledge(self, alert_ids: list[str] | None = None) -> int:
        """Mark alerts as heard; None means all of them. Returns how many."""
        ...


class NoAlerts:
    """For a receptionist who sends nothing through the post office (Mazda)."""

    def pending(self) -> list[dict]:
        return []

    def acknowledge(self, alert_ids: list[str] | None = None) -> int:
        return 0
