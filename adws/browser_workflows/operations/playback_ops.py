"""Run a saved playback, steer a running one, read the history, and apply a
repair the Mechanic proposed -- each one operation."""
from __future__ import annotations

from typing import Any, Callable

from ..interfaces.browser import IBrowserAttachment, IBrowserLease
from ..interfaces.playback import IApprovalGate, IPlaybackControl, IPlaybackExecutor, IRepairApplier, ISecretSource
from ..interfaces.storage import ILocatorRepairStore, IPlaybackRepository, IPlaybackRunLog
from ..interfaces.templating import IParameterSource, IRecordingBinder
from .common import Operation, OperationOutcome, endpoint, ok, refused
from .gates import FlagGateChooser

ExecutorFor = Callable[[ IBrowserLease, IApprovalGate, ISecretSource ], IPlaybackExecutor ]
CONTROL     = ( "pause", "resume", "stop" )


class ExecutePlayback( Operation ):
    """A templated playback's blanks are filled from --period / --params
    before the browser is touched; one left empty refuses the run."""
    name        = "execute_playback"
    description = "Replay a saved playback in the debug Chrome, step by step"

    def __init__( self, attachment: IBrowserAttachment, executor_for: ExecutorFor, gates: FlagGateChooser,
                  repository: IPlaybackRepository, download_directory: Callable[[], str ],
                  binder: IRecordingBinder, parameters: IParameterSource ) -> None:
        self._attachment   = attachment
        self._executor_for = executor_for
        self._gates        = gates
        self._repository   = repository
        self._downloads    = download_directory
        self._binder       = binder
        self._parameters   = parameters

    def perform( self, args: dict[ str, Any ]) -> OperationOutcome:
        recording = self._repository.by_name( str( args.get( "name" ) or "" ))
        if recording is None:
            return refused( f"no playback called { args.get( 'name' )!r}" )
        values    = self._parameters.values( args )
        blanks    = self._binder.placeholders( recording )
        recording = self._binder.bind( recording, values )
        filled    = ", ".join( f"{ n }={ values[ n ]}" for n in blanks )
        directory = self._downloads()
        lease = self._attachment.open( endpoint( args ))
        try:
            lease.current().configure_downloads( directory )
            executor = self._executor_for( lease, *self._gates.gates( args ))
            run_id, state, message = executor.execute( recording, str( args.get( "actor" ) or "user" ),
                                                       int( args.get( "from_step" ) or 1 ))
        finally:
            lease.release()
        named = f"'{ recording.name }'" + ( f" ({ filled })" if filled else "" )
        line  = f"{ named } run { run_id }: { state } -- { message }; download folder: { directory }"
        return ( ok if state == "completed" else refused )( line, run_id=run_id, state=state, download_directory=directory )


class ControlPlayback( Operation ):
    name        = "control_playback"
    description = "Pause, resume or stop a running playback by its run id"

    def __init__( self, control: IPlaybackControl, run_log: IPlaybackRunLog ) -> None:
        self._control = control
        self._run_log = run_log

    def perform( self, args: dict[ str, Any ]) -> OperationOutcome:
        command = str( args.get( "command" ) or "" )
        if command not in CONTROL:
            return refused( f"command must be one of { ', '.join( CONTROL )}" )
        run_id = int( args.get( "run_id" ) or 0 )
        run    = self._run_log.run( run_id )
        if run is None:
            return refused( f"no run { run_id }" )
        if run.status not in ( "ready", "playing", "paused" ):
            return refused( f"run { run_id } already ended ({ run.status })" )
        self._control.set( run_id, "run" if command == "resume" else command )
        return ok( f"run { run_id }: { command } requested" )


class PlaybackHistory( Operation ):
    name        = "playback_history"
    description = "Recent runs, or one run's step-by-step results"

    def __init__( self, run_log: IPlaybackRunLog, repository: IPlaybackRepository ) -> None:
        self._run_log    = run_log
        self._repository = repository

    def perform( self, args: dict[ str, Any ]) -> OperationOutcome:
        if args.get( "run_id" ):
            run = self._run_log.run( int( args[ "run_id" ]))
            if run is None:
                return refused( f"no run { args[ 'run_id' ]}" )
            lines = [ f"run { run.id } '{ run.name }' by { run.actor }: { run.status } -- { run.message }" ]
            for r in run.results:
                lines.append( f"  { str( r.position ).rjust( 3 )} { r.action_type.ljust( 12 )} { r.status.ljust( 8 )} { r.message }" )
                if r.diagnostics and r.diagnostics.screenshot:
                    lines.append( f"      evidence: { r.diagnostics.screenshot }" )
                if r.diagnostics and r.diagnostics.repair_note:
                    lines.append( f"      repair: { r.diagnostics.repair_note }" )
            return ok( "\n".join( lines ), run=run.model_dump())
        recording_id = None
        if args.get( "name" ):
            recording = self._repository.by_name( str( args[ "name" ]))
            if recording is None:
                return refused( f"no playback called { args[ 'name' ]!r}" )
            recording_id = recording.id
        runs = self._run_log.recent( int( args.get( "limit" ) or 20 ), recording_id )
        if not runs:
            return ok( "no runs yet", runs=[])
        lines = [ f"{ str( r.id ).rjust( 5 )} { r.started_at[ :19 ]} { r.name.ljust( 30 )} { r.status.ljust( 10 )} { r.message }" for r in runs ]
        return ok( "\n".join( lines ), runs=[ r.model_dump( exclude={ "results" }) for r in runs ])


class RepairLocator( Operation ):
    """--run-id N shows what the Mechanic proposed; --apply ID --yes saves
    one. Only a verified proposal can be saved, and only with --yes."""
    name        = "repair_locator"
    description = "Show or apply a verified locator repair for a failed step"

    def __init__( self, run_log: IPlaybackRunLog, store: ILocatorRepairStore, applier: IRepairApplier ) -> None:
        self._run_log = run_log
        self._store   = store
        self._applier = applier

    def perform( self, args: dict[ str, Any ]) -> OperationOutcome:
        if args.get( "apply" ):
            applied, line = self._applier.apply( int( args[ "apply" ]), bool( args.get( "yes" )))
            return ( ok if applied else refused )( line )
        run = self._run_log.run( int( args.get( "run_id" ) or 0 ))
        if run is None:
            return refused( "say which run: --run-id N (or --apply REPAIR_ID --yes)" )
        lines = []
        for r in run.results:
            if r.diagnostics and r.diagnostics.repair_id:
                proposal = self._store.get( r.diagnostics.repair_id )
                if proposal:
                    lines.append( f"repair { proposal.id } for step { r.position }: { proposal.candidate.strategy }="
                                  f"{ proposal.candidate.value } ({ 'verified' if proposal.verified else 'NOT verified' }"
                                  f"{ ', applied' if proposal.applied else '' }) -- { proposal.reason }" )
        return ok( "\n".join( lines ) if lines else f"run { run.id } has no proposed repairs" )
