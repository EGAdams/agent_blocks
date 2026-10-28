"""ReceiveMessage -- take the next reply waiting in an agent's inbox."""
from __future__ import annotations

from typing import Any

from ..models import MessageKind
from ..ports import IMessageTransport
from ._named import Named
from .base import OperationOutcome


class ReceiveMessage( Named ):
    NAME        = "receive_message"
    DESCRIPTION = "Take the next reply waiting in an agent's inbox"

    def __init__( self, transport: IMessageTransport ) -> None:
        self._transport = transport

    def run( self, args: dict[ str, Any ]) -> OperationOutcome:
        agent = str( args[ "agent" ])
        for waiting in self._transport.box( agent, "new" ):
            if waiting.kind is MessageKind.REQUEST:
                continue
            claimed = self._transport.claim( agent, waiting.id )
            if claimed is None:
                continue
            taken = self._transport.complete( agent, claimed )
            return OperationOutcome( ok=True, line=f"From { taken.sender }: { taken.text }",
                                     data={ "message": taken.model_dump( mode="json" )})
        return OperationOutcome( ok=True, line=f"Nothing waiting for { agent }.", data={ "message": None })
