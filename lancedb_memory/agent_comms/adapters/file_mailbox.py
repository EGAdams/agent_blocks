"""FileMailboxTransport -- Swarm-Forge's handoff mailboxes, in Python.

What is kept from Swarm-Forge (swarmforge/handoff-protocol.md):

* every agent owns `inbox/{ new, in_process, completed, failed, cancelled }`;
  a task's status IS the box it sits in, so a restart needs no state but the
  directory tree;
* filenames sort by priority, then time (`50_20261028T172800Z_ab12cd34_from_sydney.json`),
  so "the next task" is the first name in `new/`;
* writes are atomic: written under `tmp/`, flushed, then renamed into place,
  so a reader never sees half a message;
* the body is authoritative, the filename is only for ordering and audit;
* the sender keeps a copy in `sent/`, which is the audit trail of who asked.

What is deliberately different:

* No daemon. Swarm-Forge needs one because its agents are tmux panes that may
  not touch each other's worktrees; here delivery is one rename the sender can
  do itself, and waking a worker is `IWorkerLauncher`'s job, not the transport's.
* Claiming is an atomic rename of one named file, and losing the race is a
  normal outcome (None), so two workers for one agent never run a task twice.
  Swarm-Forge instead allows exactly one in-process task per role.
* JSON bodies instead of `field: value` headers, because the envelope is a
  Pydantic model and that is the validation boundary.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

from ..models import Envelope, TaskStatus, utc_now
from ..state import BOX_FOR, BOXES, advance


class FileMailboxTransport:
    """IMessageTransport over a directory tree rooted at `root`."""

    def __init__( self, root: Path ) -> None:
        self._root = Path( root )

    # -- delivery ------------------------------------------------------------

    def deliver( self, envelope: Envelope ) -> Envelope:
        queued = envelope.model_copy( update={ "status": TaskStatus.QUEUED, "enqueued_at": utc_now()})
        self._write( self._box( envelope.recipient, "new" ), queued )
        if queued.sender != queued.recipient:
            self._write( self._agent( queued.sender ) / "sent", queued )
        return queued

    # -- the queue -----------------------------------------------------------

    def claim_next( self, agent: str ) -> Envelope | None:
        for path in sorted( self._box( agent, "new" ).glob( "*.json" )):
            claimed = self._claim_path( agent, path )
            if claimed is not None:
                return claimed
        return None

    def claim( self, agent: str, envelope_id: str ) -> Envelope | None:
        path = self._path_of( agent, "new", envelope_id )
        return self._claim_path( agent, path ) if path else None

    def complete( self, agent: str, envelope: Envelope ) -> Envelope:
        return self._settle( agent, envelope, TaskStatus.SUCCEEDED )

    def fail( self, agent: str, envelope: Envelope ) -> Envelope:
        return self._settle( agent, envelope, TaskStatus.FAILED )

    def requeue( self, agent: str, envelope: Envelope ) -> Envelope:
        return self._settle( agent, envelope, TaskStatus.QUEUED )

    def cancel( self, agent: str, envelope_id: str ) -> Envelope | None:
        """Withdraw a task nobody has claimed. A running task cannot be cancelled
        here -- it may already have changed files -- and that is reported, not
        hidden: the caller gets None and can look the task up."""
        path = self._path_of( agent, "new", envelope_id )
        if path is None:
            return None
        target = self._box( agent, "cancelled" ) / path.name
        try:
            os.rename( path, target )          # atomic: either we withdraw it or a worker claims it
        except FileNotFoundError:
            return None
        envelope = self._read( target )
        if envelope is None:
            return None
        cancelled = advance( envelope, TaskStatus.CANCELLED )
        self._rewrite( target, cancelled )
        return cancelled

    # -- lookup --------------------------------------------------------------

    def find( self, envelope_id: str ) -> tuple[ str, str, Envelope ] | None:
        if not self._root.exists():
            return None
        for agent_dir in sorted( p for p in self._root.iterdir() if p.is_dir()):
            for box in BOXES:
                path = self._path_of( agent_dir.name, box, envelope_id )
                if path is not None:
                    envelope = self._read( path )
                    if envelope is not None:
                        return agent_dir.name, box, envelope
        return None

    def box( self, agent: str, box: str ) -> list[ Envelope ]:
        directory = self._agent( agent ) / ( "sent" if box == "sent" else f"inbox/{ box }" )
        found = ( self._read( p ) for p in sorted( directory.glob( "*.json" )))
        return [ e for e in found if e is not None ]

    # -- internals -----------------------------------------------------------

    def _claim_path( self, agent: str, path: Path ) -> Envelope | None:
        target = self._box( agent, "in_process" ) / path.name
        try:
            os.rename( path, target )          # atomic; losing the race is normal
        except FileNotFoundError:
            return None
        envelope = self._read( target )
        if envelope is None:
            return None
        running = advance( envelope, TaskStatus.RUNNING )
        self._rewrite( target, running )
        return running

    def _settle( self, agent: str, envelope: Envelope, to: TaskStatus ) -> Envelope:
        return self._move( agent, envelope, "in_process", advance( envelope, to ))

    def _move( self, agent: str, before: Envelope, from_box: str, after: Envelope ) -> Envelope:
        source = self._path_of( agent, from_box, before.id )
        self._write( self._box( agent, BOX_FOR[ after.status ]), after )
        if source is not None:
            source.unlink( missing_ok=True )
        return after

    def _agent( self, agent: str ) -> Path:
        return self._root / agent

    def _box( self, agent: str, box: str ) -> Path:
        path = self._agent( agent ) / "inbox" / box
        path.mkdir( parents=True, exist_ok=True )
        return path

    def _path_of( self, agent: str, box: str, envelope_id: str ) -> Path | None:
        directory = self._agent( agent ) / "inbox" / box
        matches = list( directory.glob( f"*_{ envelope_id }.json" )) if directory.exists() else []
        return matches[ 0 ] if matches else None

    @staticmethod
    def _filename( envelope: Envelope ) -> str:
        return f"{ envelope.priority:02d}_{ envelope.id }.json"

    def _write( self, directory: Path, envelope: Envelope ) -> None:
        directory.mkdir( parents=True, exist_ok=True )
        tmp_dir = directory.parent / "tmp" if directory.parent.name == "inbox" else directory / "tmp"
        tmp_dir.mkdir( parents=True, exist_ok=True )
        tmp = tmp_dir / f"{ uuid.uuid4().hex }.tmp"
        with open( tmp, "w", encoding="utf-8" ) as handle:
            handle.write( envelope.model_dump_json( indent=2 ))
            handle.flush()
            os.fsync( handle.fileno())
        os.replace( tmp, directory / self._filename( envelope ))

    def _rewrite( self, path: Path, envelope: Envelope ) -> None:
        tmp = path.with_suffix( ".rewrite" )
        tmp.write_text( envelope.model_dump_json( indent=2 ), encoding="utf-8" )
        os.replace( tmp, path )

    @staticmethod
    def _read( path: Path ) -> Envelope | None:
        try:
            return Envelope.model_validate_json( path.read_text( encoding="utf-8" ))
        except ( FileNotFoundError, ValueError ):
            return None
