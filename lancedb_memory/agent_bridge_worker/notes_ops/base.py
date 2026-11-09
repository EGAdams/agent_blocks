"""Shared pieces every notes Command uses: errors, the interfaces it is written against, outcome builders."""
from __future__ import annotations

from typing import Protocol

from ..models import AgentOutcome, ClaimedTask, OutcomeKind

PERFORMER = "notes"


class NoteChanged( Exception ):
    """The note's revision was not the one read just before the write."""


class NotesDenied( Exception ):
    """The Notes interface refused the call: no token, a bad token, a missing scope, or a note not approved."""

    def __init__( self, code: str, status: int ) -> None:
        super().__init__( f"{ code } ({ status })" )
        self.code, self.status = code, status


class NotesRefusal( Exception ):
    """The request is well-formed but this command will not do it (bad edit, wrong phrase...). Carries the words."""


# -- the dashboard's authorized interface, split by what a caller needs (Interface Segregation) ----------

class INotesBridge( Protocol ):
    """Read and overwrite approved notes (`/api/notes-bridge/v1/notes`)."""

    def list( self, task_id: str ) -> list[ str ]: ...
    def read( self, filename: str, task_id: str ) -> tuple[ str, str ]: ...                        # ( text, revision )
    def write( self, filename: str, text: str, expected_revision: str, task_id: str ) -> str: ...  # new revision


class INotesCatalog( Protocol ):
    """Look things up: search the notes, read their highlights, see the trash."""

    def entries( self, query: str, category: int | None, task_id: str ) -> list[ dict ]: ...
    def highlights( self, filename: str, task_id: str ) -> dict: ...         # { revision, highlights, review_marks }
    def trash_list( self, task_id: str ) -> list[ dict ]: ...                 # [{ trash_id, filename }]


class INotesLifecycle( Protocol ):
    """Create, rename/move, trash, restore and purge whole notes. Names are file names; see names.py."""

    def create( self, hint: str, text: str, task_id: str ) -> dict: ...                      # { filename, revision }
    def rename( self, filename: str, expected_revision: str, new_filename: str, task_id: str ) -> dict: ...
    def trash( self, filename: str, expected_revision: str, task_id: str ) -> dict: ...       # { trash_id, filename, revision }
    def restore( self, trash_id: str, task_id: str ) -> dict: ...                             # { filename, revision }
    def purge( self, trash_id: str, authorization: str, task_id: str ) -> dict: ...           # { trash_id, purged }


class INotesGateway( INotesBridge, INotesCatalog, INotesLifecycle, Protocol ):
    """Everything the dashboard's interface offers; what HttpNotesBridge is."""


class INotesActivity( Protocol ):
    def record( self, event: str, **fields: str ) -> None: ...


class INotesEndpoint( Protocol ):
    """The dashboard's read endpoint, exactly as the browser sees it."""

    def served( self, filename: str ) -> tuple[ str, str ] | None: ...
    def listed( self ) -> list[ str ]: ...


class NotesContext:
    """Facade over the collaborators a command needs, so a command takes one argument, not four."""

    def __init__( self, gateway: INotesGateway, endpoint: INotesEndpoint, activity: INotesActivity | None,
                  keep_alive=None ) -> None:
        self.gateway, self.endpoint, self._activity = gateway, endpoint, activity
        # The task's heartbeat (returns "continue" | "cancel" | "lost"); a long command calls it while it waits.
        self.keep_alive = keep_alive or ( lambda: "continue" )

    def log( self, task: ClaimedTask, event: str, **fields: str ) -> None:
        if self._activity is not None:
            self._activity.record( event, task_id=task.task_id, op=_op( task ), **fields )


def _op( task: ClaimedTask ) -> str:
    import json
    try:
        return str( json.loads( task.prompt ).get( "notes", "" ))[ :20 ]
    except ( ValueError, AttributeError ):
        return ""


# -- outcomes ------------------------------------------------------------------------------------------

def done( summary: str, details: dict, body: str | None = None ) -> AgentOutcome:
    return AgentOutcome( kind=OutcomeKind.SUCCEEDED, performer=PERFORMER, summary=summary[ :300 ],
                         body=summary if body is None else body, details=details )


def said_body( said: str, **fields ) -> str:
    """A result body: the structured fields for a caller that chains calls, plus `said` -- the sentence a
    receptionist reads aloud (Toyota speaks `said` when it is there; ChatGPT reads the fields)."""
    import json
    return json.dumps({ **fields, "said": said })


def failed( summary: str, details: dict | None = None ) -> AgentOutcome:
    return AgentOutcome( kind=OutcomeKind.FAILED, performer=PERFORMER, summary=summary[ :300 ], body=summary,
                         retryable=False, details=details )


def refused( summary: str ) -> AgentOutcome:
    return AgentOutcome( kind=OutcomeKind.REFUSED, performer=PERFORMER, summary=f"refused by the worker: { summary }" )


def yes( flag: bool ) -> str:
    return "yes" if flag else "NO"


def conflict( ctx: NotesContext, task: ClaimedTask, request, why: str, current: str | None = None ) -> AgentOutcome:
    ctx.log( task, "conflict", filename=request.id or "", expected_revision=request.expected_revision or "",
             revision=current or "", detail=why )
    return AgentOutcome( kind=OutcomeKind.FAILED, performer=PERFORMER, retryable=False,
                         summary=f"{ request.id } changed since it was read ({ why }); nothing was written. "
                                 f"Read it again and resend the update with the new revision.",
                         details={ "notes": request.notes, "id": request.id, "conflict": True,
                                   "expected_revision": request.expected_revision, "current_revision": current })
