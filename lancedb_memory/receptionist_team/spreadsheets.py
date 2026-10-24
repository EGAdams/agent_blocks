"""What a spreadsheet request IS, and the role that reads one out of words.

The value and its reader live here, apart from the runner that uses them, for
the reason this package keeps splitting things out: the dangerous part of
building a spreadsheet is not running the workflow, it is deciding WHICH
category was meant. That decision is one object with no database, no
subprocess and no model in it, so it can be read in full and tested in full.

Protocol-only but for the model it carries, matching agent_memory/ports.py's
posture -- adapters/category_spreadsheet_runner.py holds the regex
implementation, and nothing here imports it.
"""
from typing import Protocol

from pydantic import BaseModel, Field


class SpreadsheetAsk(BaseModel):
    """One understood request for one category's spreadsheet.

    A pydantic BaseModel rather than a dataclass because it crosses a
    boundary between injected collaborators -- the reader builds it, the flag
    builder spells it -- which is this workspace's rule for structured data
    on a boundary (solid_agent_systems/dependency_inversion_master).

    `category` and `category_id` are both optional and at least one must be
    set; the reader enforces that by returning None instead of an Ask, which
    is a better refusal than an Ask that names nothing. `out_format` is
    pattern-checked because an unknown format would reach the ADW as an
    argparse error with no operator line, and a workflow that writes no line
    is the one case the runners cannot report well.
    """

    category: str = ""
    category_id: int | None = None
    year: int | None = None
    include_children: bool = False
    compare: bool = False
    out_format: str = Field(default="xlsx", pattern="^(xlsx|csv)$")

    def wanted(self) -> "SpreadsheetAsk":
        """The ask with anything it cannot actually have taken back off.

        Only one rule so far: comparing to last year needs a year to compare
        from. The ADW refuses the pair on its own, but refusing here means
        the user is told what to add instead of being handed the workflow's
        refusal for something they never typed.
        """
        return self.model_copy(update={"compare": self.compare and self.year is not None})

    def named(self) -> str:
        """How to refer to what was asked for, in a sentence."""
        return self.category or f"category id {self.category_id}"


class ISpreadsheetAskReader(Protocol):
    """Turn the user's own words into an Ask, or into None.

    None means "no category was named". It is a real answer and not a
    failure: the receptionist can ask which category far more kindly than the
    workflow's own argparse can, and nothing should be run to find that out.
    """

    def read(self, text: str) -> SpreadsheetAsk | None: ...
