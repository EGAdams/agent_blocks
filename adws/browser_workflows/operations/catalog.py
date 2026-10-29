"""Every browser-workflow operation, built from one WorkflowKit, by name.

`execute_saved_playback` is not a class: it is an OperationSequence of the
load, validate and execute operations, the mission's composed ADW."""
from __future__ import annotations

from agent_comms.operations.base import OperationSequence

from ..interfaces.presenting import IConsole
from ..kit import WorkflowKit
from browser_session.profile_dir import WindowsLocalAppDataProfile

from ..normalizing.consequences import KeywordConsequenceClassifier
from ..normalizing.scrubber import SensitiveValueScrubber
from .browser_ops import ExecuteClick, ExecuteStep, ExecuteText, LocateElement, Navigate, WaitFor
from .gates import FlagGateChooser
from .install_ops import INSTALL_FOLDER, InstallExtension
from .library_ops import AgentRequest, EditPlayback, ListRecordings, LoadRecording, ValidateRecording
from .playback_ops import ControlPlayback, ExecutePlayback, PlaybackHistory, RepairLocator
from .recording_ops import ( CaptureClick, CaptureText, DiscardRecording, NormalizeEvent, RecorderStatus,
                             SaveRecording, StartRecording, StopRecording )


def operations( kit: WorkflowKit, console: IConsole ) -> dict[ str, object ]:
    gates      = FlagGateChooser( console )
    attachment = kit.attachment()
    repository = kit.repository()
    client     = kit.recorder_client()
    adhoc      = lambda approvals, secrets: kit.step_runner( approvals, secrets, repairs=False )
    playback   = ExecutePlayback( attachment, kit.playback_executor, gates, repository, kit.download_directory )
    found      = [
        RecorderStatus( client ), StartRecording( client, kit.service_launcher()), StopRecording( client ),
        SaveRecording( client ), DiscardRecording( client ), CaptureClick( client ), CaptureText( client ),
        NormalizeEvent( SensitiveValueScrubber( kit.sensitivity()), kit.normalizer()),
        LocateElement( attachment, kit.resolver()),
        ExecuteClick( attachment, adhoc, gates, KeywordConsequenceClassifier()),
        ExecuteText( attachment, adhoc, gates, kit.resolver()),
        WaitFor( attachment, kit.condition_waiter()), Navigate( attachment, kit.condition_waiter()),
        ExecuteStep( attachment, adhoc, gates, repository ),
        playback, ControlPlayback( kit.playback_control(), kit.run_log()),
        PlaybackHistory( kit.run_log(), repository ),
        RepairLocator( kit.run_log(), kit.repair_store(), kit.repair_applier()),
        ListRecordings( repository ), LoadRecording( repository, kit.editor()),
        ValidateRecording( repository, kit.validator()), EditPlayback( repository, kit.editor()),
        AgentRequest( repository, kit.matcher(), playback ),
        InstallExtension( WindowsLocalAppDataProfile( INSTALL_FOLDER )),
    ]
    catalog = { op.name: op for op in found }
    catalog[ "execute_saved_playback" ] = OperationSequence(
        "execute_saved_playback", "Load, validate, then replay a saved playback",
        [ catalog[ "load_recording" ], catalog[ "validate_recording" ], playback ])
    return catalog
