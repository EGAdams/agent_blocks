"""Which approval gate and secret source a run gets, from its flags.

  --interactive    a person at this terminal answers (approval and secrets)
  --approve 4,7    exactly those steps are pre-approved; secrets block
  (neither)        nothing consequential runs and no secret is supplied --
                   what every agent gets
"""
from __future__ import annotations

from typing import Any

from ..interfaces.playback import IApprovalGate, ISecretSource
from ..interfaces.presenting import IConsole
from ..playback.gates import ConsoleApprovalGate, ConsoleSecretSource, DenyApprovalGate, NoSecretSource, PreApprovedGate
from .common import positions


class FlagGateChooser:
    def __init__( self, console: IConsole ) -> None:
        self._console = console

    def gates( self, args: dict[ str, Any ]) -> tuple[ IApprovalGate, ISecretSource ]:
        if args.get( "interactive" ):
            return ConsoleApprovalGate( self._console ), ConsoleSecretSource( self._console )
        approved = positions( args.get( "approve" ))
        if args.get( "yes" ):
            approved.add( 0 )                           # an ad hoc step is position 0
        return ( PreApprovedGate( approved ) if approved else DenyApprovalGate()), NoSecretSource()
