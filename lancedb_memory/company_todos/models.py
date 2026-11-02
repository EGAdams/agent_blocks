"""The company to-do list's records. Pydantic, because they cross the ADW boundary."""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class TodoState( str, Enum ):
    OPEN = "open"
    DONE = "done"


class Todo( BaseModel ):
    """One item, as of one moment. The ledger keeps every version of it."""

    model_config = ConfigDict( extra="forbid" )

    number:   int = Field( ge=1 )         # what people say: "number 3"
    text:     str = Field( min_length=1 )
    state:    TodoState = TodoState.OPEN
    added_at: datetime
    added_by: str
    done_at:  datetime | None = None
    done_by:  str | None = None


class TodoAction( str, Enum ):
    ADD     = "add"
    LIST    = "list"
    DONE    = "done"
    UNCLEAR = "unclear"


class TodoCommand( BaseModel ):
    """What one request asked for, read without a model."""

    model_config = ConfigDict( extra="forbid" )

    action:  TodoAction
    text:    str = ""                     # add: the item; done: how it was named
    number:  int | None = None            # done: "#3", "number 3"
    reason:  str = ""                     # unclear: why nothing was changed
