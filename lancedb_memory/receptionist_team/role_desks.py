"""IRoleAssignment -- which receptionist's desk a routable role sits on.

Toyota and Sydney share one merged ROLES dict (roles.py) and split it between
them. Neither roster should be defined in terms of the other's list -- Sydney
built as "whatever Toyota doesn't have" means Sydney's module has to know
Toyota's internals, and a third receptionist would have to know both. So each
roster asks one injected assignment who owns a role, and takes its own.

Pure data plus one rule, no SDK import, same posture as profiles.py.
"""
from collections.abc import Iterable, Mapping
from typing import Protocol, TypeVar

Role = TypeVar("Role")


class IRoleAssignment(Protocol):
    """Names the desk (a profile name, e.g. "toyota") that owns a role."""

    def owner_of(self, agent_name: str) -> str: ...


class NamedRolesOrDefault:
    """The named roles go to one desk; every other role goes to the default.

    The default is where a brand-new agent folder lands without anyone having
    to remember to put it somewhere -- Sydney's desk, since creating agents is
    her job. A new finance agent is one more name on the finance list.
    """

    def __init__(self, named: Iterable[str], named_desk: str, default_desk: str) -> None:
        self._named = frozenset(named)
        self._named_desk = named_desk
        self._default_desk = default_desk

    def owner_of(self, agent_name: str) -> str:
        return self._named_desk if agent_name in self._named else self._default_desk


def roles_for(desk: str, roles: Mapping[str, Role], assignment: IRoleAssignment) -> dict[str, Role]:
    """The roles one desk owns, in ROLES' declaration order."""
    return {name: role for name, role in roles.items() if assignment.owner_of(name) == desk}
