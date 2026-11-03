"""RosterMembership -- which routable roles one receptionist actually offers.

Toyota used to be "every finance_team role, plus a list", so a role added to
finance_team/roles.py became hers without anyone deciding it should. Her
roster is now an explicit, validated list in toyota_roster.json, separate from
role_desks.py's desk assignment (which still decides what Sydney gets, so her
roster is unchanged by any of this).

The rules, all checked when the config loads (at import), never at the moment a
user asks for the work:
- every enabled name must already be a defined role (a folder registered in
  extra_roles.py, or a finance_team role) with a blurb, spoken name and start
  task -- the Roster itself checks the last three. Nothing here creates,
  repairs, elevates or enables anything that isn't already defined;
- a disabled name must also be a defined role, and must say what it does, so a
  disabled route is documented rather than forgotten;
- a name cannot be both enabled and disabled.

Pure data plus validation, no SDK import, same posture as profiles.py.
"""
import json
from collections.abc import Mapping
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

Role = TypeVar("Role")

TOYOTA_ROSTER_CONFIG = Path(__file__).with_name("toyota_roster.json")


class RosterMembershipError(ValueError):
    """The roster config names something that isn't a defined, routable role."""


class DisabledRoute(BaseModel):
    """A route deliberately switched off for this receptionist, and why it exists."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    purpose: str = Field(min_length=3)
    note: str = ""


class RosterMembership(BaseModel):
    """One receptionist's explicit roster: what is on it and what is switched off."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    owner: str = Field(min_length=1)
    enabled: tuple[str, ...]
    disabled: dict[str, DisabledRoute] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _no_name_is_both(self) -> "RosterMembership":
        if len(set(self.enabled)) != len(self.enabled):
            raise ValueError("a role is listed twice under enabled")
        both = set(self.enabled) & set(self.disabled)
        if both:
            raise ValueError(f"enabled and disabled at once: {', '.join(sorted(both))}")
        return self

    def select(self, roles: Mapping[str, Role]) -> dict[str, Role]:
        """The enabled roles, in config order. Unknown names fail loud."""
        unknown = [name for name in (*self.enabled, *self.disabled) if name not in roles]
        if unknown:
            raise RosterMembershipError(
                f"{self.owner}'s roster names roles that are not defined: {', '.join(unknown)}. "
                "Define the role first (an agents/ folder registered in extra_roles.py); "
                "the roster config never creates one."
            )
        return {name: roles[name] for name in self.enabled}

    def is_disabled(self, name: str) -> bool:
        return name in self.disabled


def load_membership(path: Path) -> RosterMembership:
    """Read and validate a roster config file."""
    return RosterMembership.model_validate(json.loads(path.read_text(encoding="utf-8")))
