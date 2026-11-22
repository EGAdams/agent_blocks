"""BATI desktop Chrome: the policy and the values that cross between the pieces. Data only."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BatiPolicy(BaseModel):
    """Everything this package is allowed to do, as data. Narrow on purpose: one machine, one port, one origin."""
    model_config = ConfigDict(frozen=True)

    machine:   str = "BATI"
    hostname:  str = "DESKTOP-SHDBATI"         # WSL's hostname AND Windows' COMPUTERNAME must both say this
    port:      int = 9222                      # the debug Chrome's DevTools port on Windows' 127.0.0.1
    windows_python: str = "/mnt/c/Python313/python.exe"
    origins:   tuple[str, ...] = ("http://localhost:8765", "http://127.0.0.1:8765")   # the dashboard, via BATI's proxy
    views:     tuple[str, ...] = ("plan-for-today",)    # dashboard tabs show_view may press; nothing else is clickable
    launch_wait_seconds: float = 25.0
    view_wait_seconds:   float = 20.0
    relay_call_seconds:  float = 15.0
    operation_seconds:   int   = 120           # the whole run; past it the run is a timeout, alerted


class Outcome(BaseModel):
    """What one operation reports. `verified` is True only when the effect was read back, not assumed."""
    adw_id:    str
    operation: str
    ok:        bool
    verified:  bool = False
    message:   str
    cause:     str = ""                        # a short code on failure: wrong_machine, no_desktop, timeout, ...
    data:      dict[str, Any] = Field(default_factory=dict)
    started:   str = ""
    finished:  str = ""
