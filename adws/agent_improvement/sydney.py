"""Sydney as an improvement target: her worker, her judge, where her prompt lives, and what she is asked.

Nothing in the engine names Sydney. This module is the only place her prompt path, her Bridge route
and her task catalog are written down, and `profiles.py` hands them to the engine as a TargetProfile.

    worker   ProbeAgentRunner        one isolated turn of the real Sydney (receptionist_team.improvement_probe)
    judge    BridgeTaskEvaluator     rule-based; reads the turn's route, Bridge phases and reply
    trainer  ReferenceEvidence       the reference schema of the agent she is asked to address
    where    PromptFileEnvironment   pilot: a staged tree; live: her real file, then her service restarted
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

from . import rsi
from .models import Evidence, TaskRole, TaskSpec
from .patching import sha256

SUITE_VERSION = "sydney-bridge-tasks/1"

#: The prompt section a candidate may change, and the text that ends it.
SECTION_START = "THE STAR TREK BRIDGE"
SECTION_END   = "\nLIMITS"
PLACEHOLDERS  = ( "{{roster}}", "{{allowed_agents}}" )
PROMPT_RELPATH = "lancedb_memory/receptionist_team/agents/sydney/system_prompt_template/system_prompt.md"

ROUTE_PREFIX  = "route."
BRIDGE_PREFIX = "bridge."


# --------------------------------------------------------------------------- tasks

def sydney_tasks() -> list[ TaskSpec ]:
    """Real, repeatable, read-only Bridge asks. Nothing here prints, scans, writes money or mails a person."""
    return [
        TaskSpec( name="machine-ops-status", role=TaskRole.TRAINING,
                  input_text=( "Through the Bridge, have machine ops check the status of the adw-supervisor "
                               "service, and tell me the task id." ),
                  expect={ "route": "star_trek_bridge", "agent": "machine-ops", "workspace": "machine_ops",
                           "payload": { "operation": [ "status" ], "unit": "adw-supervisor.service" },
                           "succeeds": True }),
        TaskSpec( name="machine-ops-health-variation", role=TaskRole.HELD_OUT,
                  input_text=( "Ask machine ops on the Star Trek Bridge whether the dashboard server is up "
                               "and answering, and give me the task id." ),
                  expect={ "route": "star_trek_bridge", "agent": "machine-ops", "workspace": "machine_ops",
                           "payload": { "operation": [ "check", "status" ], "unit": "dashboard-server.service" },
                           "succeeds": True }),
        TaskSpec( name="hello-world", role=TaskRole.REGRESSION,
                  input_text="Over the Bridge, send hello-world a hi and tell me the task id.",
                  expect={ "route": "star_trek_bridge", "agent": "hello-world", "succeeds": True }),
        TaskSpec( name="no-bridge-needed", role=TaskRole.REGRESSION,
                  input_text="In one sentence, what kinds of work do you handle?",
                  expect={ "route": "none" }),
    ]


class StaticTaskCatalog:
    """Implements ITaskCatalog."""

    def __init__( self, version: str, tasks: list[ TaskSpec ]):
        self.version = version
        self._tasks  = list( tasks )

    def tasks( self ) -> list[ TaskSpec ]:
        return list( self._tasks )


# --------------------------------------------------------------------------- worker

class ProbeAgentRunner( rsi.IAgentRunner ):
    """The worker: one turn of the real receptionist, run as `improvement_probe` in a child process.

    The revision's body comes out of the RSI store, so a run is always of a recorded revision.
    Token counts are the model's own when it reports them, otherwise `prompt_chars / 4`, and the
    trace says which (`usage.estimate`).
    """

    def __init__( self, *, agent: str, store: Any, argv: list[ str ], cwd: Path, work_root: Path,
                  wait_seconds: float, timeout_seconds: float,
                  run_process: Callable[ ..., subprocess.CompletedProcess ] = subprocess.run ):
        self._agent, self._store, self._argv, self._cwd = agent, store, list( argv ), cwd
        self._work_root, self._wait, self._timeout = work_root, wait_seconds, timeout_seconds
        self._run_process = run_process

    @property
    def runtime_kind( self ) -> rsi.AgentRuntimeKind:
        return rsi.AgentRuntimeKind.GENERIC

    def run( self, request: rsi.RunRequest ) -> rsi.RunResult:
        wrapper = self._store.revisions.get_revision( self._agent, request.wrapper_revision )
        body    = self._store.body( self._agent, wrapper.system_message_revision )
        safe_id = re.sub( r"[^A-Za-z0-9_.-]", "_", request.run_id )
        payload = { "agent": self._agent, "system_prompt_template": body, "input_text": request.input_text,
                    "work_dir": str( self._work_root / safe_id ), "wait_seconds": self._wait,
                    "session_id": f"probe-{ safe_id }"[ :80 ]}
        try:
            done = self._run_process( self._argv, input=json.dumps( payload ), capture_output=True, text=True,
                                      cwd=str( self._cwd ), timeout=self._timeout )
        except subprocess.TimeoutExpired:
            return self._failed( request, f"the turn took longer than { self._timeout:g}s" )
        answer = _last_json( done.stdout )
        if not answer.get( "ok" ):
            return self._failed( request, str( answer.get( "error" ) or f"probe exited { done.returncode }" ))
        return _result( request, answer )

    @staticmethod
    def _failed( request: rsi.RunRequest, error: str ) -> rsi.RunResult:
        return rsi.RunResult( run_id=request.run_id, agent_name=request.agent_name, task_name=request.task_name,
                              output_text="", succeeded_transport=False, error=error[ :600 ])


def _last_json( stdout: str ) -> dict[ str, Any ]:
    for line in reversed(( stdout or "" ).strip().splitlines()):
        try:
            value = json.loads( line )
        except ValueError:
            continue
        if isinstance( value, dict ):
            return value
    return { "ok": False, "error": "the probe wrote no JSON" }


def _result( request: rsi.RunRequest, answer: dict[ str, Any ]) -> rsi.RunResult:
    calls: list[ rsi.ToolCallRecord ] = []
    for item in answer.get( "delegations", []):
        calls.append( rsi.ToolCallRecord( order_index=len( calls ), tool_name=ROUTE_PREFIX + str( item.get( "agent" )),
                                          arguments_json=json.dumps({ "extra": item.get( "extra", "" )}),
                                          result_summary="ran" if item.get( "ran" ) else "recorded, not run" ))
    bridge = answer.get( "bridge" ) or {}
    for phase in bridge.get( "trace", []):
        calls.append( rsi.ToolCallRecord( order_index=len( calls ), tool_name=BRIDGE_PREFIX + str( phase.get( "name" )),
                                          arguments_json=json.dumps( phase.get( "inputs", {})),
                                          result_summary=json.dumps({ "status": phase.get( "status" ),
                                                                      **( phase.get( "outputs" ) or {})}),
                                          error=phase.get( "error" ) or None ))
    measured  = int( answer.get( "input_tokens" ) or 0 ) + int( answer.get( "output_tokens" ) or 0 )
    estimated = measured or int( answer.get( "prompt_chars" ) or 0 ) // 4
    calls.append( rsi.ToolCallRecord( order_index=len( calls ), tool_name="usage.estimate",
                                      arguments_json=json.dumps({ "source": "model" if measured else "prompt_chars/4",
                                                                  "task_id": bridge.get( "task_id" ) or "",
                                                                  "model": answer.get( "model", "" )}),
                                      result_summary=str( estimated )))
    return rsi.RunResult( run_id=request.run_id, agent_name=request.agent_name, task_name=request.task_name,
                          output_text=str( answer.get( "reply_text" ) or "" ), tool_calls=calls,
                          token_usage=rsi.TokenUsage( prompt_tokens=estimated, total_tokens=estimated ))


# --------------------------------------------------------------------------- judge

class BridgeTaskEvaluator( rsi.IEvaluator ):
    """The judge. Rules over what the turn did, by task name; it never asks the agent under test.

    Checks (each named so a regression or an improvement is a check that changed):
        route                the route the task needs was chosen (or none was, for "none")
        bridge_target        the Bridge validated the intended agent and workspace
        payload_strict_json  the text after "agent@workspace:" is one JSON object with only allowed keys
        payload_matches      its operation and unit are the ones the task asked for
        task_id_reported     a submitted task's id appears in the reply
        result_succeeded     the Bridge retrieved the task and it succeeded
        no_false_success     a task that did not succeed is not described as done
    """

    ALLOWED_KEYS = frozenset({ "operation", "unit", "lines", "suite" })

    def __init__( self, catalog: Any ):
        self._tasks = { task.name: task for task in catalog.tasks()}

    def evaluate( self, trace: rsi.TraceRecord ) -> rsi.ScoreCard:
        task = self._tasks.get( trace.task_name )
        if task is None:
            return rsi.ScoreCard( run_id="", failed_checks=[ "known_task" ], notes=f"no task { trace.task_name }" )
        expect = task.expect
        calls  = { call.tool_name: call for call in trace.tool_calls }
        routes = [ name[ len( ROUTE_PREFIX ): ] for name in calls if name.startswith( ROUTE_PREFIX )]
        passed: list[ str ] = []
        failed: list[ str ] = []

        def check( name: str, ok: bool ) -> None:
            ( passed if ok else failed ).append( name )

        if expect.get( "route" ) == "none":
            check( "route", "star_trek_bridge" not in routes )
            return _card( passed, failed, trace )
        check( "route", expect.get( "route" ) in routes )
        validate = _summary( calls.get( BRIDGE_PREFIX + "validate" ))
        if "agent" in expect:
            check( "bridge_target", validate.get( "agent" ) == expect[ "agent" ]
                   and validate.get( "workspace", expect.get( "workspace" )) == expect.get( "workspace", validate.get( "workspace" )))
        if "payload" in expect:
            extra = json.loads( calls[ ROUTE_PREFIX + "star_trek_bridge" ].arguments_json ).get( "extra", "" ) \
                if ROUTE_PREFIX + "star_trek_bridge" in calls else ""
            body  = _payload( extra )
            check( "payload_strict_json", isinstance( body, dict ) and set( body ) <= self.ALLOWED_KEYS )
            want  = expect[ "payload" ]
            check( "payload_matches", isinstance( body, dict ) and body.get( "operation" ) in want[ "operation" ]
                   and body.get( "unit" ) == want[ "unit" ])
        submit  = _summary( calls.get( BRIDGE_PREFIX + "submit" ))
        task_id = str( submit.get( "task_id" ) or "" )
        check( "task_id_reported", bool( task_id ) and task_id in trace.agent_output )
        if expect.get( "succeeds" ):
            got = _summary( calls.get( BRIDGE_PREFIX + "retrieve" ))
            ok  = bool( got.get( "ok" ))
            check( "result_succeeded", ok )
            check( "no_false_success", ok or not re.search( r"\b(succeeded|completed successfully|is done)\b",
                                                            trace.agent_output, re.I ))
        return _card( passed, failed, trace )


def _summary( call: rsi.ToolCallRecord | None ) -> dict[ str, Any ]:
    if call is None or not call.result_summary:
        return {}
    try:
        value = json.loads( call.result_summary )
    except ValueError:
        return {}
    return value if isinstance( value, dict ) else {}


def _payload( extra: str ) -> Any:
    _, sep, rest = extra.partition( ":" )
    if not sep:
        return None
    try:
        return json.loads( rest.strip())
    except ValueError:
        return None


def _card( passed: list[ str ], failed: list[ str ], trace: rsi.TraceRecord ) -> rsi.ScoreCard:
    total = len( passed ) + len( failed )
    return rsi.ScoreCard( run_id="", metrics={ "pass_rate": ( len( passed ) / total ) if total else 0.0 },
                          passed_checks=passed, failed_checks=failed,
                          notes=trace.agent_output[ :400 ])


# --------------------------------------------------------------------------- trainer

class ReferenceEvidence:
    """Implements IEvidenceCollector: reference files, quoted before anything is proposed.

    For Sydney, the schema of the agent she is asked to address. The baseline phase adds what the
    baseline itself did; this collects what was true before it ran. Each excerpt is bounded, and
    nothing secret is in these files.
    """

    def __init__( self, references: list[ tuple[ str, Path, str ]]):
        self._references = references            # ( label, path, regex selecting the lines to quote )

    def collect( self, cycle: Any ) -> list[ Evidence ]:
        found: list[ Evidence ] = []
        for label, path, pattern in self._references:
            if not path.is_file():
                found.append( Evidence( source=f"reference:{ label }", text="(not found on this machine)" ))
                continue
            lines = [ line for line in path.read_text( encoding="utf-8" ).splitlines() if re.search( pattern, line )]
            found.append( Evidence( source=f"reference:{ label }", text="\n".join( lines )[ :1500 ]))
        return found


# --------------------------------------------------------------------------- where the prompt lives

class PromptFileEnvironment:
    """Implements IArtifactEnvironment over a prompt file and a way to see what the target loaded.

    `reload` is run after a write (the live service restart; nothing for a staged tree, which is read
    fresh by every probe). `loaded_check` asks the real profile code in that tree what it built.
    """

    def __init__( self, *, name: str, agent: str, path: Path, loaded_check: list[ str ], cwd: Path,
                  reload: list[ str ] | None = None, requires_person: bool = False,
                  run_process: Callable[ ..., subprocess.CompletedProcess ] = subprocess.run ):
        self.name, self._agent, self._path, self._check, self._cwd = name, agent, path, list( loaded_check ), cwd
        self._reload, self.requires_person, self._run_process = reload, requires_person, run_process

    def read( self ) -> str:
        return self._path.read_text( encoding="utf-8" )

    def write( self, body: str ) -> None:
        temp = self._path.with_suffix( ".md.tmp" )
        temp.write_text( body, encoding="utf-8" )
        temp.replace( self._path )
        if sha256( self.read()) != sha256( body ):
            raise RuntimeError( f"read-back of { self._path } does not match what was written" )
        if self._reload:
            done = self._run_process( self._reload, capture_output=True, text=True, timeout=60 )
            if done.returncode != 0:
                raise RuntimeError( f"reload failed ({ done.returncode }): { done.stderr.strip()[ :300 ]}" )

    def loaded( self, expected_body: str ) -> tuple[ str, bool ]:
        """( sha256 of the prompt the target's own profile built, whether it is `expected_body` rendered )."""
        request = json.dumps({ "agent": self._agent, "report_loaded": True, "system_prompt_template": expected_body })
        done    = self._run_process( self._check, input=request, capture_output=True, text=True,
                                     cwd=str( self._cwd ), timeout=120 )
        answer  = _last_json( done.stdout )
        if not answer.get( "ok" ):
            raise RuntimeError( f"loaded-revision check failed: { answer.get( 'error' )}" )
        return str( answer[ "loaded_sha256" ]), bool( answer[ "matches" ])

    def describe( self ) -> str:
        return f"{ self.name }: { self._path }" + ( f" (then: { ' '.join( self._reload )})" if self._reload else "" )
