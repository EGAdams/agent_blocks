"""ObjectInspectionRoute -- the four inspection questions go to the object inspector, never to memory.

"Show the latest Notes update", "Where did this task stop?", "Show this
object's state and error" and "Did the content save, and did the browser
render that version?" are questions about what really happened, and the only
honest answer is the trace (object_trace, served on 127.0.0.1:8790). Left to
the model they would be answered from recalled turns, the same failure
BridgeTaskRoute and FigureQuestionRoute exist for.

Goes right after BridgeConfirmationRoute and AHEAD of BridgeTaskRoute: that
route reads "where did task t_... stop?" as a cancel (bridge_client.workflow
treats "stop" as one), so this route must claim the question first.
"""
import re

from .latest_user import latest_user_message

TASK_ID = re.compile(r"\bt_[0-9a-f]{24}\b")
# object ids are "<kind>:<rest>", kind dotted: agent.notes:t_..:1, bridge.task_run:t_..:2, sssf.run:adw_x
OBJECT_ID = re.compile(r"\b[a-z][a-z0-9_]*\.[a-z0-9_.]+:[A-Za-z0-9_.:\-]+")

_ABOUT_A_RUN = re.compile(r"\b(?:task|run|job|workflow|adw|it|that|this)\b", re.I)

_QUESTIONS = (
    ("saved_and_rendered", re.compile(r"\b(?:sav(?:e|ed|ing))\b.*\brender|\brender(?:ed|s)?\b.*\b(?:version|revision)\b",
                                      re.I | re.S)),
    ("latest_notes_update", re.compile(r"\b(?:latest|last|most\s+recent|newest)\s+notes?\s+(?:update|write|change|edit)s?\b",
                                       re.I)),
    ("where_stopped", re.compile(r"\bwhere\s+(?:did|does|has|is|was)\b.*\b(?:stop(?:ped)?|stall(?:ed)?|stuck|halt(?:ed)?|fail(?:ed)?)\b",
                                 re.I | re.S)),
    ("object_state", re.compile(r"\bobject(?:'s|s)?\b.*\b(?:state|error|status)\b|\b(?:state|error)\b.*\bobject\b",
                                re.I | re.S)),
)


def inspection_question(text: str) -> tuple[str, str | None] | None:
    """(inspector question name, subject id or None), or None when the words ask none of the four."""
    if not text:
        return None
    name = next((name for name, pattern in _QUESTIONS if pattern.search(text)), None)
    if name is None:
        return None
    if name == "where_stopped" and not (TASK_ID.search(text) or _ABOUT_A_RUN.search(text)):
        return None  # "where is the stop button" is not about a task
    if name == "object_state":
        found = OBJECT_ID.search(text)
        return name, found.group(0).rstrip(".:") if found else None
    found = TASK_ID.search(text)
    return name, found.group(0) if found else None


class ObjectInspectionRoute:
    def __init__(self, agent_name: str = "object_inspector") -> None:
        self._agent = agent_name

    def forced_agent(self, prompt: str) -> str | None:
        return self._agent if inspection_question(latest_user_message(prompt)) else None

    def question(self, prompt: str) -> str:
        return latest_user_message(prompt)
