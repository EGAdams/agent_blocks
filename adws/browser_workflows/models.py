"""The values that cross between browser_workflows' pieces. Data only.

Raw events are what the extension saw; actions are what playback does. Keeping
them apart is the point of the normaliser: the page's vocabulary changes when
Chrome does, playback's only when we decide it should.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> str:
    return datetime.now( timezone.utc ).isoformat( timespec="seconds" )


# -- Locating ----------------------------------------------------------------

LocatorStrategyName = Literal[ "css", "text", "label" ]


class LocatorCandidate( BaseModel ):
    """One recorded way of finding an element, as data.

    `strategy` picks the ILocatorStrategy; `source` says where the value came
    from (test_id, name, aria_label, id, text, css_path, repair ...), which is
    what decides its rank and what a person editing the step reads.
    """
    model_config = ConfigDict( extra="forbid" )

    strategy: LocatorStrategyName
    value:    str
    tag:      str = ""
    source:   str = "css"
    rank:     int = 50


class ElementSummary( BaseModel ):
    """A visible interactive element on the page now, for diagnostics."""
    tag:   str
    text:  str = ""
    css:   str
    attrs: dict[ str, str ] = Field( default_factory=dict )


# -- Recording ---------------------------------------------------------------

RawEventKind = Literal[ "click", "text_committed", "select", "check", "key", "navigation", "tab_switch" ]


class RawBrowserEvent( BaseModel ):
    """One thing the page saw, as the extension reports it.

    `value` is None whenever `sensitive` is true: the content script never
    sends a secret, and the Python side re-checks before anything is stored.
    """
    model_config = ConfigDict( extra="ignore" )

    kind:            RawEventKind
    url:             str = ""
    title:           str = ""
    tab_id:          int | None = None
    timestamp:       float = 0.0
    tag:             str = ""
    input_type:      str = ""
    text:            str = ""
    value:           str | None = None
    checked:         bool | None = None
    key:             str = ""
    sensitive:       bool = False
    sensitive_reason: str = ""
    field_hints:     dict[ str, str ] = Field( default_factory=dict )   # name, id, autocomplete, label, aria
    locators:        list[ LocatorCandidate ] = Field( default_factory=list )
    transition_type: str = ""
    form_action:     str = ""


ActionType = Literal[ "navigate", "click", "enter_text", "select_option", "set_checked",
                      "press_key", "wait", "switch_tab" ]

WaitKind = Literal[ "element_visible", "element_enabled", "url_equals", "url_contains",
                    "text_present", "navigation_complete" ]


class WaitCondition( BaseModel ):
    """An explicit condition, never a fixed delay."""
    model_config = ConfigDict( extra="forbid" )

    kind:            WaitKind
    value:           str = ""
    timeout_seconds: float = 15.0


class RecordedAction( BaseModel ):
    """One reusable step, before it has a database id."""
    model_config = ConfigDict( extra="forbid" )

    action_type:       ActionType
    url:               str = ""
    locators:          list[ LocatorCandidate ] = Field( default_factory=list )
    value:             str | None = None
    value_is_secret:   bool = False
    requires_approval: bool = False
    wait_after:        list[ WaitCondition ] = Field( default_factory=list )
    metadata:          dict[ str, Any ] = Field( default_factory=dict )
    recorded_at:       str = Field( default_factory=utc_now )


class RecordingStep( RecordedAction ):
    """A stored action: it has an id and a position in its recording."""
    id:       int
    position: int


class Recording( BaseModel ):
    model_config = ConfigDict( extra="forbid" )

    id:          int
    name:        str
    description: str = ""
    start_url:   str = ""
    created_at:  str = ""
    updated_at:  str = ""
    steps:       list[ RecordingStep ] = Field( default_factory=list )


class RecordingSummary( BaseModel ):
    id:         int
    name:       str
    step_count: int
    start_url:  str = ""
    updated_at: str = ""


RecordingStateName = Literal[ "idle", "recording", "saving" ]


class RecordingSessionInfo( BaseModel ):
    id:           str
    state:        RecordingStateName
    tab_id:       int | None = None
    start_url:    str = ""
    title:        str = ""
    started_at:   str = ""
    ended_at:     str = ""
    event_count:  int = 0
    recording_id: int | None = None


class ControllerStatus( BaseModel ):
    """What the recorder service says about itself."""
    state:               RecordingStateName
    extension_connected: bool
    session:             RecordingSessionInfo | None = None
    message:             str = ""


# -- Validation --------------------------------------------------------------

class ValidationIssue( BaseModel ):
    position: int | None = None
    severity: Literal[ "error", "warning" ]
    message:  str


class ValidationReport( BaseModel ):
    issues: list[ ValidationIssue ] = Field( default_factory=list )

    @property
    def ok( self ) -> bool:
        return not any( i.severity == "error" for i in self.issues )


# -- Playback ----------------------------------------------------------------

PlaybackStateName = Literal[ "ready", "playing", "paused", "completed", "failed", "blocked", "stopped" ]
StepStatus = Literal[ "ok", "failed", "blocked", "skipped" ]


class LocatorAttempt( BaseModel ):
    """What one recorded locator found."""
    label:   str
    outcome: Literal[ "found", "none", "ambiguous", "hidden", "disabled", "error" ]
    count:   int = 0


class Resolution( BaseModel ):
    found:    bool
    winner:   str = ""
    attempts: list[ LocatorAttempt ] = Field( default_factory=list )


class StepDiagnostics( BaseModel ):
    url:             str = ""
    title:           str = ""
    screenshot:      str = ""
    attempts:        list[ LocatorAttempt ] = Field( default_factory=list )
    similar:         list[ ElementSummary ] = Field( default_factory=list )
    error:           str = ""
    repair_id:       int | None = None
    repair_note:     str = ""


class StepResult( BaseModel ):
    run_id:      int
    step_id:     int
    position:    int
    action_type: str
    status:      StepStatus
    strategy:    str = ""
    message:     str = ""
    diagnostics: StepDiagnostics | None = None
    started_at:  str = Field( default_factory=utc_now )
    ended_at:    str = Field( default_factory=utc_now )


class PlaybackRun( BaseModel ):
    id:           int
    recording_id: int
    name:         str = ""
    actor:        str = ""
    status:       PlaybackStateName
    started_at:   str = ""
    ended_at:     str = ""
    message:      str = ""
    results:      list[ StepResult ] = Field( default_factory=list )


class RepairProposal( BaseModel ):
    id:        int | None = None
    step_id:   int
    run_id:    int | None = None
    candidate: LocatorCandidate
    verified:  bool = False
    applied:   bool = False
    reason:    str = ""
