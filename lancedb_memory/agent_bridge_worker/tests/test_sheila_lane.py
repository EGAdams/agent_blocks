"""Sheila on BATI's machine lane, offline: the lane builds her as a PostOfficeAgent in her own workspace.

    cd lancedb_memory
    .venv/bin/python agent_bridge_worker/tests/test_sheila_lane.py

Real registry (copied into a tmpdir with her sandbox moved there), real post office, real ScopedPolicy; only the
gateway is a double. No network, no Pi.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path( __file__ ).resolve().parents[ 3 ]
sys.path.insert( 0, str( ROOT / "lancedb_memory" ))

from agent_bridge_worker.adapters.post_office_agent import PostOfficeAgent
from agent_bridge_worker.cashy_agent import Machine
from agent_bridge_worker.models import ClaimedTask
from agent_bridge_worker.policy import ScopedPolicy
from agent_comms.adapters.launchers import RecordingLauncher
from agent_comms.factory import REGISTRY, build_post_office
from agent_comms.tests.fakes import FakeClock

spec = importlib.util.spec_from_file_location( "adw_bridge_worker", ROOT / "adws" / "adw_bridge_worker.py" )
worker = importlib.util.module_from_spec( spec ); spec.loader.exec_module( worker )


class FakeGateway:
    def __init__( self ) -> None:
        self.events = []

    def event( self, task, kind, text ) -> None:
        self.events.append(( kind, text ))


def built():
    tmp  = Path( tempfile.mkdtemp( prefix="sheila_lane_" ))
    data = json.loads( REGISTRY.read_text( encoding="utf-8" ))
    for card in data[ "agents" ]:
        if card[ "name" ] == "sheila":
            card[ "settings" ][ "write_workdir" ] = str( tmp / "sheila_sandbox" )
    registry = tmp / "agents.json"; registry.write_text( json.dumps( data ), encoding="utf-8" )
    kit = build_post_office( root=tmp / "po", registry_path=registry, launcher=RecordingLauncher(), clock=FakeClock(),
                             forward_failures_to_mechanic=False )
    bati = Machine.load( worker.MACHINES_FILE, "DESKTOP-SHDBATI" )
    agents, caps, workdirs = { bati.agent: object()}, { bati.agent: { "inspect": False, "implement": True }}, { bati.agent: tmp / "bati" }
    events = worker.machine_post_office_agents( bati, FakeGateway(), FakeClock(), agents, caps, workdirs, kit=kit )
    return tmp, agents, caps, workdirs, events


def task( agent: str, op: str, workspace: str, mode: str ) -> ClaimedTask:
    return ClaimedTask.model_validate({ "task_id": "t_" + "1" * 24, "lease_token": "l_" + "2" * 32, "lease_expires_at": 1,
                                        "lease_seconds": 600, "attempt": 1, "agent": agent, "operation": op,
                                        "workspace": workspace, "approval_mode": mode, "prompt": '{"sheila": "status"}' })


def test_bati_lane_hosts_sheila_beside_the_desktop_agent() -> None:
    tmp, agents, caps, workdirs, events = built()
    assert set( agents ) == { "bati-desktop", "sheila" } and isinstance( agents[ "sheila" ], PostOfficeAgent )
    assert caps[ "sheila" ] == { "inspect": False, "explain": False, "implement": True }
    assert workdirs[ "sheila" ] == tmp / "sheila_sandbox" and workdirs[ "sheila" ].is_dir()
    assert events is agents[ "sheila" ]                         # her post office's audit trail feeds CollectEvents


def test_the_lane_policy_takes_her_three_operations_and_nothing_else() -> None:
    _, agents, caps, _, _ = built()
    policy = ScopedPolicy( caps, frozenset( agents ), frozenset({ "read_only", "workspace_write" }))
    assert policy.refusal( task( "sheila", "inspect", "sheila", "read_only" )) is None
    assert policy.refusal( task( "sheila", "implement", "sheila", "workspace_write" )) is None
    assert policy.refusal( task( "sheila", "implement", "agent_blocks_dev", "workspace_write" ))   # only her sandbox
    assert policy.refusal( task( "sheila", "scan", "sheila", "read_only" ))


def test_r46_lane_builds_no_post_office_agent() -> None:
    assert Machine.load( worker.MACHINES_FILE, "Rosemary46" ).post_office_agents == ()


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted( globals().items()):
        if name.startswith( "test_" ):
            try:
                fn(); print( "ok  ", name )
            except Exception as error:
                failed += 1; print( "FAIL", name, type( error ).__name__, error )
    sys.exit( 1 if failed else 0 )
