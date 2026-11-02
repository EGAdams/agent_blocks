"""Liz's own city-tax roster, on the shared receptionist engine."""
from .agents import AgentRoleFactory, catalog
from .rosters import Roster

LIZ_ROSTER = Roster(
    owner="Liz",
    roles={"city_tax_desk": AgentRoleFactory().role(catalog().definition("city_tax_desk"))},
    blurbs={"city_tax_desk": "prepares the 2025 Grand Rapids city return: forms, comparison, figures, scans, review"},
    spoken_names={"city_tax_desk": "the city tax desk"},
    start_tasks={"city_tax_desk": "working on the city return"},
)
LIZ_SYSTEM_PROMPT = catalog().definition("liz").system_prompt().render({
    "roster": LIZ_ROSTER.roster_text(),
    "allowed_agents": LIZ_ROSTER.allowed_agents(),
})
