"""Roster -- the specialists one receptionist can route to, and the words she
uses to talk about them.

Toyota routes to finance_team's real roles; Mazda routes to stubs until her
Letta sub-agents are wired in. The two rosters hold different agents, but the
*wording* of a delegation acknowledgment and of the system prompt's roster
block is identical for both -- so that wording lives here once and each
receptionist's module builds one Roster instance (roles.py -> TOYOTA_ROSTER,
mazda_roles.py -> MAZDA_ROSTER).

A frozen dataclass rather than a pydantic BaseModel, for the same reason
AgentRole is one: this is configuration assembled at import time, not data
crossing a process boundary.
"""
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Roster:
    """One receptionist's specialists, plus how she speaks about each one.

    Every agent needs all three descriptions, and a missing one is a wiring
    mistake rather than a runtime condition -- so __post_init__ raises at
    import time instead of letting a KeyError surface mid-conversation, which
    is what the three hand-written dict checks in roles.py used to do.
    """

    owner: str                           # receptionist name, for error messages
    roles: Mapping[str, object]          # agent name -> AgentRole
    blurbs: Mapping[str, str]            # what the agent does, for the prompt
    spoken_names: Mapping[str, str]      # "our receipt scanner", for speech
    start_tasks: Mapping[str, str]       # phrase that follows "tell them to start"

    def __post_init__(self) -> None:
        for label, mapping in (
            ("blurbs", self.blurbs),
            ("spoken_names", self.spoken_names),
            ("start_tasks", self.start_tasks),
        ):
            missing = set(self.roles) - set(mapping)
            if missing:
                raise RuntimeError(
                    f"{self.owner} roster: {label} is missing an entry for: "
                    f"{sorted(missing)}"
                )

    def names(self) -> tuple[str, ...]:
        """Routable agent names in declaration order -- how the prompt lists them.

        Declaration order, not sorted: the roster block reads as a deliberate
        running order (the four mappers, then the synthesizer that consumes
        them), and sorting it would scramble that into alphabetical noise.
        """
        return tuple(self.roles)

    def sorted_names(self) -> tuple[str, ...]:
        """Agent names alphabetically -- for lists the user reads as a set."""
        return tuple(sorted(self.roles))

    def has(self, agent_name: str) -> bool:
        return agent_name in self.roles

    def role(self, agent_name: str) -> object:
        return self.roles[agent_name]

    def allowed_agents(self) -> str:
        """The comma-separated list the system prompt names as legal ROUTE targets."""
        return ", ".join(self.sorted_names())

    def roster_text(self) -> str:
        """The prompt's roster block: one `name (spoken name): what it does` line."""
        return "\n".join(
            f"{name} ({self.spoken_names[name]}): {self.blurbs[name]}"
            for name in self.names()
        )

    def unknown_agent_reply(self, agent_name: str) -> str:
        """What the receptionist says when the model routes to a name she lacks.

        A friendly sentence rather than a crash: the model picked the name, so
        a typo in a generated control line must not end the conversation.
        """
        return (
            f'I tried to route that to "{agent_name}", but I don\'t have '
            f"an agent by that name. I know: {', '.join(self.sorted_names())}."
        )

    def acknowledgment(
        self,
        agent_name: str,
        task_description: str | None = None,
    ) -> str:
        """Build the sentence spoken immediately before delegation.

        The voice handler should start playing this acknowledgment before
        starting the delegated task, then let playback and delegation overlap.
        Do not wait for the agent's results before playing this sentence.

        An optional task_description must be a short spoken phrase that fits
        after "start", such as "checking the status of your yearly spreadsheet".
        Do not pass raw routing instructions or technical logs as this phrase.
        """
        if agent_name not in self.roles:
            raise ValueError(f"Unknown {self.owner} agent: {agent_name!r}")

        task = (
            task_description.strip()
            if task_description and task_description.strip()
            else self.start_tasks[agent_name]
        )
        task = task.rstrip(".!?")

        return (
            "Please wait a few seconds while I contact "
            f"{self.spoken_names[agent_name]} "
            f"and tell them to start {task}."
        )
