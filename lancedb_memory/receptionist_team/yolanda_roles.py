"""Yolanda's own email roster, on the shared receptionist engine."""
from .agents import AgentRoleFactory, catalog
from .rosters import Roster

YOLANDA_ROSTER = Roster(
    owner="Yolanda",
    roles={"email_tools": AgentRoleFactory().role(catalog().definition("email_tools"))},
    blurbs={"email_tools": "operates the currently configured email system; Gmail first"},
    spoken_names={"email_tools": "our email connector"},
    start_tasks={"email_tools": "handling your email request"},
)
YOLANDA_SYSTEM_PROMPT = catalog().definition("yolanda").system_prompt().render({
    "roster": YOLANDA_ROSTER.roster_text(),
    "allowed_agents": YOLANDA_ROSTER.allowed_agents(),
})
