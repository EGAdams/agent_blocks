"""What a notes task prompt may say. Pure data; no behaviour beyond shape checks."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MARKER    = r"^NOTESDIAG-[0-9a-f]{12}$"
REVISION  = r"^[0-9a-f]{64}$"
NOTE_ID   = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}\.md$"
TRASH_ID  = r"^\d{14}_(?:\d+_)?[A-Za-z0-9][A-Za-z0-9._-]{0,200}\.md$"
MAX_EDITS = 50

OPERATIONS = ( "status", "marker_write", "marker_clear",
               "list", "search", "read", "highlights", "trash_list", "refresh",
               "update", "highlight", "create", "rename", "trash", "restore", "purge",
               # the review operations (review.py), each one run of the Notes Review ADW
               "review_inspect", "review_get_active_highlight", "review_verify", "review_snapshots", "review_watch",
               "review_stop_blink", "review_activate", "review_activate_next", "review_edit", "review_mark_resolved",
               "review_rollback" )
ITEM_ID     = r"^r-[0-9a-f]{10}$"
SNAPSHOT_ID = r"^s-[0-9a-f]{10}$"


class NoteEdit( BaseModel ):
    """One change to a note's text. Exactly one locator: `find` (a unique exact substring),
    `line` (1-based), `section` (a heading's text) or `append` (text added at the end). `replace` is the new text (empty deletes a `find`)."""

    model_config = ConfigDict( extra="forbid", frozen=True )

    find:    str | None = Field( default=None, min_length=1 )
    line:    int | None = Field( default=None, ge=1 )
    section: str | None = Field( default=None, min_length=1, max_length=200 )
    append:  str | None = Field( default=None, min_length=1 )          # add to the end (build a long note in chunks)
    replace: str = ""

    @model_validator( mode="after" )
    def one_locator( self ) -> "NoteEdit":
        if sum( x is not None for x in ( self.find, self.line, self.section, self.append )) != 1:
            raise ValueError( "an edit names exactly one of find, line, section or append" )
        return self


class HighlightChange( BaseModel ):
    """add / recolor / clear one highlight span, located by its exact text; `all` clears every span."""

    model_config = ConfigDict( extra="forbid", frozen=True )

    action: Literal[ "add", "recolor", "clear" ]
    text:   str | None = Field( default=None, min_length=1, max_length=2000 )
    color:  str | None = Field( default=None, pattern=r"^[a-z]{3,10}$" )
    all:    bool = False


class NotesRequest( BaseModel ):
    """The one prompt shape this agent accepts."""

    model_config = ConfigDict( extra="forbid", frozen=True )

    notes:             Literal[ OPERATIONS ]                                          # type: ignore[valid-type]
    marker:            str | None = Field( default=None, pattern=MARKER )
    id:                str | None = Field( default=None, pattern=NOTE_ID )
    expected_revision: str | None = Field( default=None, pattern=REVISION )
    known_revision:    str | None = Field( default=None, pattern=REVISION )
    edits:             list[ NoteEdit ] | None = Field( default=None, min_length=1, max_length=MAX_EDITS )
    marks:             list[ HighlightChange ] | None = Field( default=None, min_length=1, max_length=MAX_EDITS )
    text:              str | None = None
    query:             str | None = Field( default=None, min_length=1, max_length=200 )
    category:          int | None = Field( default=None, ge=0, le=10 )               # 0 on rename = no category
    name:              str | None = Field( default=None, min_length=1, max_length=120 )
    new_name:          str | None = Field( default=None, min_length=1, max_length=120 )
    trash_id:          str | None = Field( default=None, pattern=TRASH_ID )
    authorization:     str | None = Field( default=None, max_length=400 )
    # -- review operations (review.py) --
    item_id:           str | None = Field( default=None, pattern=ITEM_ID )            # from review_inspect
    replace:           str | None = Field( default=None, min_length=1, max_length=2000 )
    evidence:          str | None = Field( default=None, min_length=1, max_length=1000 )
    approval:          str | None = Field( default=None, min_length=1, max_length=1000 )
    advance:           bool | None = None
    page:              bool | None = None
    no_page:           bool | None = None
    expect_item_id:    str | None = Field( default=None, pattern=ITEM_ID )
    snapshot_id:       str | None = Field( default=None, pattern=SNAPSHOT_ID )
    chain:             bool | None = None
    until:             Literal[ "clear", "change", "active_change" ] | None = None
    timeout:           float | None = Field( default=None, ge=1, le=600 )
    interval:          float | None = Field( default=None, ge=1, le=60 )
    limit:             int | None = Field( default=None, ge=1, le=100 )

    def given( self ) -> set[ str ]:
        """The optional fields this request actually set (everything except `notes`)."""
        return { k for k in type( self ).model_fields if k != "notes" and getattr( self, k ) is not None }
