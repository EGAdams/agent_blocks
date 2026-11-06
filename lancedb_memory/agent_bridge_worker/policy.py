"""IExecutionPolicy and IWorkspaceResolver: the worker's own locks.

The gateway's PolicyAuthorizer already granted the task. This is checked again
on the machine that would run it, because the gateway lives on shared hosting
and the worker should not do anything the gateway was tricked into allowing.
`ReadOnlyPolicy` runs read-only work only, whatever the task says.
`ScopedPolicy` adds one write operation, `implement`, and only in
`workspace_write` mode, only for an agent whose card declares it as a write,
and only in a workspace this machine's own workspaces.json enables for writes.
`scan`, `print` and `print_cancel` run only in `device_control` mode, which the
composition root enables only when this machine has the device set up;
`print_status` and `print_discover` only read, so they run in `read_only` mode.
The mode is derived from the operation here: a read operation asked for in a
write mode (or the reverse) is refused, never reinterpreted.
"""
from __future__ import annotations

from pathlib import Path

from .models import ClaimedTask

READ_ONLY_OPERATIONS = frozenset({ "inspect", "explain", "search_memory" })
WRITE_OPERATIONS     = frozenset({ "implement" })
DEVICE_OPERATIONS    = frozenset({ "scan", "print", "print_cancel" })   # drives a physical device; writes no workspace
DEVICE_READS         = frozenset({ "print_status", "print_discover" })  # asks a device's agent, changes nothing
MODE_FOR             = { **{ op: "read_only" for op in READ_ONLY_OPERATIONS | DEVICE_READS },
                         **{ op: "workspace_write" for op in WRITE_OPERATIONS },
                         **{ op: "device_control" for op in DEVICE_OPERATIONS }}


class ReadOnlyPolicy:
    """Allows a task only if it is read-only, for a known agent, in an operation that agent declares read-only."""

    def __init__( self, agent_operations: dict[ str, frozenset[ str ]], modes: frozenset[ str ] = frozenset({ "read_only" })) -> None:
        self._agents = { name: frozenset( ops ) & READ_ONLY_OPERATIONS for name, ops in agent_operations.items() }
        self._modes  = modes

    def refusal( self, task: ClaimedTask ) -> str | None:
        if task.approval_mode not in self._modes:
            return f"approval mode { task.approval_mode } is not enabled on this worker"
        if task.operation not in READ_ONLY_OPERATIONS:
            return f"operation { task.operation } is not read-only"
        allowed = self._agents.get( task.agent )
        if allowed is None:
            return f"this worker does not run tasks for { task.agent }"
        if task.operation not in allowed:
            return f"{ task.agent } does not offer { task.operation } read-only"
        return None


class StaticWorkspaces:
    """IWorkspaceResolver over a fixed name -> directory map, set in the composition root."""

    def __init__( self, workspaces: dict[ str, Path ]) -> None:
        self._map = { name: Path( path ).resolve() for name, path in workspaces.items() }

    def resolve( self, workspace: str ) -> Path:
        path = self._map.get( workspace )
        if path is None:
            raise LookupError( f"no workspace called { workspace } on this machine" )
        if not path.is_dir():
            raise LookupError( f"workspace { workspace } is not a directory here" )
        return path


def read_only_operations( registry, sender: str ) -> dict[ str, frozenset[ str ]]:
    """Each agent's read-only capabilities that `sender` may ask for, from the post-office registry."""
    out: dict[ str, frozenset[ str ]] = {}
    for name in registry.names():
        card = registry.card( name )
        if card is None or ( sender not in card.accepts_from and "*" not in card.accepts_from ):
            continue
        ops = frozenset( c.task_type for c in card.capabilities if not c.writes )
        if ops & READ_ONLY_OPERATIONS:
            out[ card.name ] = ops
    return out


class ScopedPolicy:
    """Read-only operations as before, plus `implement` in `workspace_write` mode in a write-enabled workspace."""

    def __init__( self, agent_capabilities: dict[ str, dict[ str, bool ]], write_workspaces: frozenset[ str ],
                  modes: frozenset[ str ] = frozenset({ "read_only", "workspace_write" })) -> None:
        self._agents = { name: dict( caps ) for name, caps in agent_capabilities.items() }
        self._writes = frozenset( write_workspaces )
        self._modes  = frozenset( modes )

    def refusal( self, task: ClaimedTask ) -> str | None:
        if task.sender not in { "chatgpt-bridge", "toyota-bridge" }:
            return "gateway sender is not approved"
        if task.sender == "toyota-bridge" and task.approval_mode != "read_only":
            return "toyota-bridge only has read-only gateway scope"
        expected = MODE_FOR.get( task.operation )
        if expected is None:
            return f"operation { task.operation } is not one this worker runs"
        if task.approval_mode != expected:
            return f"operation { task.operation } runs only in { expected } mode, not { task.approval_mode }"
        if task.approval_mode not in self._modes:
            return f"approval mode { task.approval_mode } is not enabled on this worker"
        caps = self._agents.get( task.agent )
        if caps is None:
            return f"this worker does not run tasks for { task.agent }"
        if task.operation not in caps:
            return f"{ task.agent } does not offer { task.operation }"
        writes = expected == "workspace_write"
        if caps[ task.operation ] is not writes:
            return f"{ task.agent } declares { task.operation } as { 'a write' if caps[ task.operation ] else 'read-only' }, which does not match { expected }"
        if writes and task.workspace not in self._writes:
            return f"workspace { task.workspace } is not write-enabled on this worker"
        return None


def scoped_operations( registry, sender: str ) -> dict[ str, dict[ str, bool ]]:
    """Each agent's bridge operations that `sender` may ask for, as {operation: writes}, from the registry.

    A read operation counts only when the card says it does not write; `implement`
    only when the card says it does. A card that gets that wrong offers nothing.
    """
    out: dict[ str, dict[ str, bool ]] = {}
    for name in registry.names():
        card = registry.card( name )
        if card is None or ( sender not in card.accepts_from and "*" not in card.accepts_from ):
            continue
        caps = { c.task_type: bool( c.writes ) for c in card.capabilities
                 if ( c.task_type in READ_ONLY_OPERATIONS and not c.writes )
                 or ( c.task_type in WRITE_OPERATIONS and c.writes )}
        if caps:
            out[ card.name ] = caps
    return out


class FixedFirstWorkspaces:
    """IWorkspaceResolver: a few names this worker owns (the scanners' empty work dir), then everything else."""

    def __init__( self, fixed: dict[ str, Path ], rest ) -> None:
        self._fixed = StaticWorkspaces( fixed )
        self._names = frozenset( fixed )
        self._rest  = rest

    def resolve( self, workspace: str ) -> Path:
        return self._fixed.resolve( workspace ) if workspace in self._names else self._rest.resolve( workspace )
