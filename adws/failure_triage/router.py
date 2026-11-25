"""The post-office adapters: ITriageRouter (to an owner agent) and INotifier (to EG through Yolanda).

Both send as `failure-triage`, a card with no runtime, and never wait for an answer. The router can only
send the task types in ALLOWED_TASK_TYPES: triage asks for a plan or a diagnosis, never a build.
"""
from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from .interfaces import ISecretCheck, RouteRefused
from .models import Cluster, OwnerRule

SENDER             = "failure-triage"
ALLOWED_TASK_TYPES = frozenset({ "plan", "diagnose", "message" })
DEFAULT_TO         = "eg@americansjewelry.com"
WORKDIR            = "/home/adamsl/agent_blocks"

Ops = dict[ str, Any ]


def default_ops() -> Ops:
    from agent_comms.factory import build_post_office
    from agent_comms.operations.catalog import operations
    return operations( build_post_office())


class _Sender:
    def __init__( self, ops_factory: Callable[ [], Ops ], secrets: ISecretCheck ) -> None:
        self._ops_factory = ops_factory
        self._ops: Ops | None = None
        self._secrets = secrets

    def _send( self, recipient: str, task_type: str, text: str, context: dict[ str, Any ]) -> str:
        if task_type not in ALLOWED_TASK_TYPES:
            raise RouteRefused( f"triage may not send a { task_type!r } task" )
        hits = self._secrets.found( text )
        if hits:
            raise RouteRefused( f"withheld: the text looked like it held { ', '.join( hits ) }" )
        if self._ops is None:
            self._ops = self._ops_factory()
        outcome = self._ops[ "send_and_capture" ].run({ "sender": SENDER, "recipient": recipient, "task_type": task_type,
                                                        "text": text, "context": context, "wait_seconds": 0 })
        task_id = str(( getattr( outcome, "data", None ) or {}).get( "task_id", "" ))
        if not getattr( outcome, "ok", False ) and not task_id:
            raise RouteRefused( str( getattr( outcome, "line", "refused" )))
        return task_id


class PostOfficeRouter( _Sender ):

    def send( self, cluster: Cluster, rule: OwnerRule ) -> str:
        if rule.owner in ( "eg", "sender" ) or not rule.task_type:
            raise RouteRefused( f"{ rule.owner } is not an agent to route to" )
        return self._send( rule.owner, rule.task_type, routed_text( cluster, rule ),
                           { "triage_cluster": cluster.id, "workdir": WORKDIR, "task_id": cluster.task_ids[ -1 ]})


class YolandaNotifier( _Sender ):

    def __init__( self, ops_factory: Callable[ [], Ops ], secrets: ISecretCheck, to: str | None = None ) -> None:
        super().__init__( ops_factory, secrets )
        self._to = to or os.getenv( "FAILURE_TRIAGE_EMAIL" ) or DEFAULT_TO

    def digest( self, subject: str, body: str ) -> str:
        return self._send( "yolanda", "message",
                           ( f"Send this email now (this is the instruction to send it; do not ask). "
                             f"To: { self._to }. Subject: { subject } Body, exactly:\n{ body }" ), {})


def routed_text( cluster: Cluster, rule: OwnerRule ) -> str:
    ids = ", ".join( cluster.task_ids[ -3: ])
    if rule.task_type == "diagnose":
        return ( f"Triage note: a new kind of failure, not yet matched by owners.json. Task { cluster.task_ids[ -1 ] } "
                 f"failed on { cluster.agent }: { cluster.sample_error[ :400 ] } "
                 f"({ cluster.count } time(s), cluster { cluster.id }; other tasks: { ids })." )
    return (
        f"Failure triage, cluster { cluster.id } ({ rule.name }). Plan only: do not change any file.\n\n"
        f"Agent { cluster.agent } has failed { cluster.count } time(s) between { cluster.first_seen:%Y-%m-%d } and "
        f"{ cluster.last_seen:%Y-%m-%d %H:%M } UTC with:\n  { cluster.sample_error[ :500 ] }\n"
        f"Most recent request (first 200 characters): { cluster.sample_request or '(none)' }\n"
        f"Task ids: { ids }. Each one's envelope is under adws/adw_data/post_office/mailboxes/{ cluster.agent }/inbox/failed/ "
        f"on SQMC, and the mechanic's diagnosis is in mechanic_cases.jsonl.\n\n"
        f"Find the cause in this repository (the agent's card is in lancedb_memory/agent_comms/agents.json and its "
        f"executor under lancedb_memory/agent_comms/executors/). Answer with: the cause, the smallest change that "
        f"fixes it, the test that proves it, and whether the fix is in code, config or how callers ask. "
        f"When a fix lands, it is recorded with: adws/adw_failure_triage_fix.py --cluster { cluster.id } --fix \"<commit> <what>\"." )
