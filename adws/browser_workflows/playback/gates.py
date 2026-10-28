"""IApprovalGate and ISecretSource: where a person stays in charge.

An agent's run gets `DenyApprovalGate` and `NoSecretSource`, so a
consequential step or a password field blocks the run and says why, instead
of being guessed through.
"""
from __future__ import annotations

from ..interfaces.presenting import IConsole
from ..models import Recording, RecordingStep


def _describe( step: RecordingStep ) -> str:
    what = step.metadata.get( "text" ) or step.metadata.get( "element_key" ) or step.url
    return f"step { step.position } ({ step.action_type } { what })".strip()


class ConsoleApprovalGate:
    def __init__( self, console: IConsole ) -> None:
        self._console = console

    def approve( self, step: RecordingStep, recording: Recording ) -> bool:
        self._console.say( f"'{ recording.name }' { _describe( step )} is marked consequential." )
        return self._console.ask( "Type YES to let it run: " ).strip() == "YES"


class DenyApprovalGate:
    def approve( self, step: RecordingStep, recording: Recording ) -> bool:
        return False


class PreApprovedGate:
    """Approves exactly the positions a person listed up front (--approve 4,7)."""

    def __init__( self, positions: set[ int ]) -> None:
        self._positions = positions

    def approve( self, step: RecordingStep, recording: Recording ) -> bool:
        return step.position in self._positions


class ConsoleSecretSource:
    def __init__( self, console: IConsole ) -> None:
        self._console = console

    def secret_for( self, step: RecordingStep ) -> str | None:
        reason = step.metadata.get( "sensitive_reason" ) or "a secret field"
        return self._console.ask_secret( f"{ _describe( step )} needs { reason }; type it (not echoed, not saved): " ) or None


class NoSecretSource:
    def secret_for( self, step: RecordingStep ) -> str | None:
        return None
