"""The operation catalog: every operation, built over one PostOfficeKit.

The ADWs and the receptionists' dispatch runner both get their operations
here, so `adws/adw_op_route_request.py` and Sydney's in-process routing run
the very same object. Composite workflows are assembled here too, from the
same pieces.
"""
from __future__ import annotations

from ..factory import PostOfficeKit
from .base import IOperation, OperationSequence
from .cancel_task import CancelTask
from .capture_response import CaptureResponse
from .create_message import CreateMessage
from .discover_agents import DiscoverAgents
from .execute_task import ExecuteTask
from .hello_world import HelloWorld
from .log_result import LogResult
from .read_capabilities import ReadCapabilities
from .receive_message import ReceiveMessage
from .report_failure import ReportFailure
from .request_mechanic import RequestMechanic
from .retry_task import RetryTask
from .route_request import RouteRequest
from .send_message import SendMessage
from .task_status import TaskStatusReport
from .validate_task import ValidateTask


def operations( kit: PostOfficeKit ) -> dict[ str, IOperation ]:
    single: list[ IOperation ] = [
        HelloWorld(),
        DiscoverAgents( kit.registry ),
        ReadCapabilities( kit.registry ),
        RouteRequest( kit.router ),
        CreateMessage(),
        ValidateTask( kit.validator ),
        SendMessage( kit.dispatcher, kit.registry ),
        CaptureResponse( kit.replies ),
        ReceiveMessage( kit.transport ),
        ExecuteTask( kit.worker_for ),
        TaskStatusReport( kit.board ),
        LogResult( kit.events ),
        ReportFailure( kit.events ),
        RetryTask( kit.board, kit.dispatcher ),
        RequestMechanic( kit.dispatcher ),
        CancelTask( kit.canceller ),
    ]
    catalog = { op.name: op for op in single }
    catalog[ "delegate_task" ] = delegate_task( catalog )
    catalog[ "send_and_capture" ] = OperationSequence(
        "send_and_capture", "Create, validate and send an explicit request, then wait for its reply",
        [ catalog[ "create_message" ], catalog[ "validate_task" ], catalog[ "send_message" ],
          catalog[ "capture_response" ]], skip_when={ "capture_response": "asynchronous" })
    return catalog


def delegate_task( catalog: dict[ str, IOperation ]) -> OperationSequence:
    """Free text to a reply: route -> validate -> send -> (capture, unless asynchronous)."""
    return OperationSequence(
        "delegate_task", "Route a free-text request to the agent it names, send it, and wait if it is quick",
        [ catalog[ "route_request" ], catalog[ "validate_task" ], catalog[ "send_message" ],
          catalog[ "capture_response" ]], skip_when={ "capture_response": "asynchronous" })
