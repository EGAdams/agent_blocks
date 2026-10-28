"""PostOfficeAlerts -- IAlertInbox over agent_comms' check_alerts operation.

The same operation `adws/adw_op_check_alerts.py` runs, so what the sidecar
reports and what the ADW prints cannot drift. check_alerts sweeps for tasks
whose worker died first, so asking is also what turns a killed worker into a
retry or a loud failure. agent_comms is imported on first use, the same
deferred posture as PostOfficeRunner.
"""
from collections.abc import Callable


def _default_kit():
    from agent_comms.factory import build_post_office
    return build_post_office()


class PostOfficeAlerts:
    def __init__(self, agent: str, kit_factory: Callable[[], object] = _default_kit) -> None:
        self._agent = agent
        self._kit_factory = kit_factory
        self._operation = None

    def pending(self) -> list[dict]:
        from agent_comms.models import Alert

        outcome = self._check().run({"agent": self._agent})
        return [{**data, "spoken": Alert.model_validate(data).spoken()} for data in outcome.data["alerts"]]

    def acknowledge(self, alert_ids: list[str] | None = None) -> int:
        self._check()
        return self._kit.alerts.acknowledge(self._agent, alert_ids)

    def _check(self):
        if self._operation is None:
            from agent_comms.operations.catalog import operations
            self._kit = self._kit_factory()
            self._operation = operations(self._kit)["check_alerts"]
        return self._operation
