"""Specialist roles Toyota can route to that are NOT in finance_team/roles.py.

That file (and finance_team/mechanic_runner.py) is off-limits to edits, so any
new specialist capability has to be defined here instead and merged into
receptionist_team.roles.ROLES -- see that module's `ROLES = {**_FINANCE_ROLES,
**EXTRA_ROLES}`. adapters/routing_model.py reads that merged dict (not
finance_team.roles.ROLES directly) so a ROUTE line naming one of these
resolves at dispatch time. Same AgentRole dataclass and run_role machinery
finance_team's own roles use -- just owned here.

What changed: the prompts no longer live in this file. Each specialist is a
folder under agents/ (system_prompt_template/, user_prompt_template/,
model_confiurations/) and this module only names which folders to register.
Adding a specialist is now a folder plus one line in EXTRA_ROLE_NAMES, with
no Python prompt literal to maintain -- see agents/README.md.
"""
from finance_team.roles import REPO_ROOT, AgentRole  # noqa: F401  (REPO_ROOT re-exported)

from .agents import AgentRoleFactory, catalog

# Agent folders registered as routable specialists. A name here that has no
# folder -- or a folder missing a prompt or an output_path -- fails loud at
# import rather than at the moment a user asks for the work.
EXTRA_ROLE_NAMES = (
    "spreadsheet_reshaper", "agent_creator", "browser_opener",
    "search_link_opener", "live_books", "mechanic_translator", "dashboard_expert",
)

_factory = AgentRoleFactory()

EXTRA_ROLES: dict[str, AgentRole] = {
    name: _factory.role(catalog().definition(name))
    for name in EXTRA_ROLE_NAMES
}
