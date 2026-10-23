"""The data that crosses the agent-definition boundary.

Pydantic `BaseModel` because this is exactly the case the house rule names:
structured data read from outside (JSON on disk) and handed to another layer.
Validation is the point -- a typo in `model_confiurations/model.json` should
fail at load with the field name, not three layers later as an AttributeError.

Behavior lives in the loaders (`model_configurations.py`, `tool_catalog.py`);
these are values only.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

# Mirrors finance_team.roles.AgentRole's own default tool list. Repeated as a
# literal rather than imported so an agent folder that never touches
# finance_team still has a sane default.
DEFAULT_ALLOWED_TOOLS = [ "Bash", "Read", "Write", "Glob", "Grep" ]


class ModelConfiguration( BaseModel ):
    """`model_confiurations/model.json` for one agent.

    (The directory name is misspelled on disk; it is the user's layout and is
    kept verbatim so the folders they created keep working. See layout.py.)

    Every field has a default, so an agent folder may ship an empty `{}` and
    still load -- a new agent is useful before it is fully specified.
    """

    description: str = ""
    model: str | None = None
    max_turns: int = 40
    allowed_tools: list[ str ] = Field( default_factory=lambda: list( DEFAULT_ALLOWED_TOOLS ))
    permission_mode: str = "bypassPermissions"
    # Where the agent's deliverable goes. finance_team.roles.AgentRole requires
    # one, so role_adapter.py refuses to build a role without it.
    output_path: str | None = None
    # Free-form for the composition root: Toyota's own entry carries nothing a
    # delegated role needs, and vice versa.
    notes: str = ""


class ToolDeclaration( BaseModel ):
    """One `tools/<name>/tool.json`.

    `status` is deliberately part of the data: most tool folders in this tree
    are stubs that name a capability nobody has implemented yet, and a catalog
    that cannot say so is worse than no catalog.
    """

    name: str
    kind: str = "stub"          # builtin | mcp | script | stub
    status: str = "stub"        # stub | implemented
    description: str = ""


class DocumentationIndex( BaseModel ):
    """What documentation an agent folder currently carries.

    Stub on purpose -- it reports which documentation directories exist and
    which are still empty, and generating the contents is a separate job (see
    documentation.py).
    """

    present: list[ str ] = Field( default_factory=list )
    empty: list[ str ] = Field( default_factory=list )
