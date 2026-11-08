"""The Browser Control catalog: operation name -> Operation. A new operation is one class and one line here."""
from __future__ import annotations

from agent_comms.operations.base import OperationSequence
from browser_workflows.operations.common import Operation

from . import operations as ops
from .downloads import Download
from .plane import ControlPlane, default_plane

ORDER = [ops.Open, ops.Navigate, ops.Refresh, ops.Tabs, ops.SelectTab, ops.Click, ops.Read, ops.Screenshot,
         ops.Status, ops.Close, ops.FormControls, ops.SelectOption, ops.Fill, Download]

#: "browser.open" (the Bridge's spelling) -> the ADW operation name.
PUBLIC = {cls.name.replace("browser_", "browser."): cls.name for cls in ORDER}
PUBLIC["browser.dashboard_notes"] = "browser_dashboard_notes"


def browser_control_operations(plane: ControlPlane | None = None) -> dict[str, Operation]:
    """browser.open is three phases, each traced on its own: launch -> visibility -> open.
    browser.dashboard_notes is six: request -> launch -> visibility -> dashboard_tab -> click -> verify."""
    plane = plane or default_plane()
    table: dict[str, Operation] = {cls.name: cls(plane) for cls in ORDER}
    table[ops.Open.name] = OperationSequence(ops.Open.name, ops.Open.description,
                                             [ops.Launch(plane), ops.Visibility(plane), ops.Open(plane)])
    table["browser_dashboard_notes"] = OperationSequence(
        "browser_dashboard_notes", "browser.dashboard_notes: open Chrome on the desktop, go to the dashboard, press NOTES, confirm it",
        [ops.NotesRequest(plane), ops.Launch(plane), ops.Visibility(plane), ops.DashboardTab(plane), ops.Click(plane),
         ops.NotesVerify(plane)])
    return table
