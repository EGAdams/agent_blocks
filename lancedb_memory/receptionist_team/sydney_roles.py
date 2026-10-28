"""Sydney's persona and roster.

Sydney is a clone of Toyota: same engine, same Groq conversation model, same
shared LanceDB memory, same RoutingModel and ToolAgentRunner, same delegation
acknowledgment and same sidecar. What differs is the work. Toyota keeps the
books; Sydney does everything else this team has built -- planning, building,
testing and deploying code, creating new agents, summoning the mechanic for
both receptionists, the browser and web search, the ADW guide, and the
second-opinion models.

Her roster is whatever roles.ROLE_ASSIGNMENT (an IRoleAssignment, see
role_desks.py) puts on the "sydney" desk. She never reads Toyota's list: both
rosters ask the same assignment, so they cannot overlap, and an agent folder
registered in extra_roles.py lands here unless it is named as a finance agent.
The blurbs, spoken names and start tasks are roles.py's own dicts -- one wording per
agent, whoever routes to it.

No SDK import, same as roles.py -- pure data plus one render.
"""
from .agents import catalog
from .role_desks import roles_for
from .roles import (
    ROLE_ASSIGNMENT,
    ROLE_BLURBS,
    ROLE_SPOKEN_NAMES,
    ROLE_START_TASKS,
    ROLES,
    TOYOTA_CHAT_MODEL,
)
from .rosters import Roster

SYDNEY_ROLES = roles_for("sydney", ROLES, ROLE_ASSIGNMENT)

SYDNEY_ROSTER = Roster(
    owner="Sydney",
    roles=SYDNEY_ROLES,
    blurbs=ROLE_BLURBS,
    spoken_names=ROLE_SPOKEN_NAMES,
    start_tasks=ROLE_START_TASKS,
)


def delegation_acknowledgment(
    agent_name: str,
    task_description: str | None = None,
) -> str:
    """Sydney's delegation acknowledgment. See Roster.acknowledgment."""
    return SYDNEY_ROSTER.acknowledgment(agent_name, task_description)


# Same model as Toyota, deliberately: she is a clone, and Groq's binding limit
# is 8000 tokens per minute shared across both gpt-oss sizes, so a smaller
# model would buy nothing -- see roles.TOYOTA_CHAT_MODEL.
SYDNEY_CHAT_MODEL = TOYOTA_CHAT_MODEL

SYDNEY_SYSTEM_PROMPT = catalog().definition("sydney").system_prompt().render(
    {
        "roster": SYDNEY_ROSTER.roster_text(),
        "allowed_agents": SYDNEY_ROSTER.allowed_agents(),
    }
)
