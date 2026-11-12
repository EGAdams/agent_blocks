"""WorkerLane: which tasks a worker process will take, so two workers can run side by side.

The gateway's queue is first-in-first-out and a worker runs one task at a time, so a
single long coding task used to hold every later task -- diagnostics, Machine Ops,
Notes -- behind it (2026-11-06: a 7m41s write task kept two short tasks "queued").

Two workers fix that only if one of them can never be the one stuck on a long task:

    general   takes everything, exactly as the single worker always did. It is the only
              lane that runs coding writes, so those still run one at a time and in order.
    express   takes everything except coding writes (`implement` in a git-backed write
              workspace). It asks the gateway to pass those over (`next?skip=...`), so it
              is always free for the next diagnostic, Machine Ops or Notes request.
    machine   takes only the workspaces the gateway pins to this machine's worker token
              (`worker_pins`, `next?pinned=1`) -- Cashy on R46 -- and nothing else. It runs on
              the machine whose desktop the task acts on, never beside the home worker.

Claims stay atomic at the gateway (a second claim of the same task is a 409), and each
lane keeps its own poll-state and health file so the dashboard and the supervisor can
tell the two processes apart. Nothing here talks to the network or the filesystem.
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

CACHE = Path.home() / ".cache" / "agent_bridge"
CODING_WRITE = "implement"


class WorkerLane( BaseModel ):
    model_config = ConfigDict( frozen=True )

    name: Literal[ "general", "express", "machine" ] = "general"

    @property
    def takes_coding_writes( self ) -> bool:
        return self.name == "general"

    @property
    def unit( self ) -> str:
        return { "general": "agent-bridge-worker.service", "express": "agent-bridge-worker-express.service",
                 "machine": "agent-bridge-worker-machine.service" }[ self.name ]

    @property
    def poll_state_path( self ) -> Path:
        return CACHE / ( "poll_state.json" if self.name == "general" else f"poll_state_{ self.name }.json" )

    @property
    def health_path( self ) -> Path:
        return CACHE / ( "worker_health.json" if self.name == "general" else f"worker_health_{ self.name }.json" )

    def default_worker_id( self, host: str ) -> str:
        return f"wsl-{ host }" if self.name == "general" else f"wsl-{ host }-{ self.name }"

    @property
    def pinned( self ) -> bool:
        """Asks the gateway for its own pinned workspaces only; the gateway passes the rest over."""
        return self.name == "machine"

    def skips( self, coding_workspaces: Iterable[ str ] ) -> list[ str ]:
        """The `operation@workspace` pairs this lane asks the gateway to pass over."""
        if self.takes_coding_writes or self.pinned:
            return []
        return [ f"{ CODING_WRITE }@{ workspace }" for workspace in sorted( set( coding_workspaces ))]

    def write_workspaces( self, coding_workspaces: Iterable[ str ], self_contained: Iterable[ str ] ) -> frozenset[ str ]:
        """Where this lane may write: coding workspaces only on `general`; self-contained ones (Notes) on both."""
        coding = frozenset( coding_workspaces ) if self.takes_coding_writes else frozenset()
        return coding | frozenset( self_contained )
