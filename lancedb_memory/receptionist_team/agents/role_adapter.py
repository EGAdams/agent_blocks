"""AgentRoleFactory -- definition on disk -> executable finance_team AgentRole.

Single responsibility: the mapping, and only the mapping:

    system_prompt_template/  ->  AgentRole.system_prompt
    user_prompt_template/    ->  AgentRole.task_prompt
    model_confiurations/     ->  AgentRole.output_path / allowed_tools / max_turns

This is the seam that lets a specialist be *defined* as a folder while still
being *run* by the machinery that already exists: `run_role` in
finance_team/adapters/tool_agent.py takes an AgentRole, RoutingModel looks a
role up by name, and neither has to learn about directories.

`AgentRole` is imported as a type, not abstracted behind an interface -- it is
a frozen data value, and the house anti-over-engineering rule says values
don't get interfaces. The import direction is the same deliberate cross-team
edge `adapters/routing_model.py` already has.
"""
from __future__ import annotations

from collections.abc import Mapping

from finance_team.roles import AgentRole

from .interfaces import IAgentDefinition


class IncompleteAgentDefinition( RuntimeError ):
    """The folder cannot be turned into a runnable role, and says which part is missing."""


class AgentRoleFactory:
    """IRoleFactory over a FileSystemAgentDefinition."""

    def role(
        self,
        definition: IAgentDefinition,
        values: Mapping[ str, str ] | None = None,
    ) -> AgentRole:
        values = values or {}
        configuration = definition.model_configuration()

        if not configuration.output_path:
            raise IncompleteAgentDefinition(
                f"Agent { definition.name!r } has no output_path in its "
                f"model configuration, so there is nowhere for its deliverable "
                f"to go. Add it to model_confiurations/model.json."
            )

        try:
            system_prompt = definition.system_prompt().render( values )
            task_prompt = definition.user_prompt().render( values )
        except FileNotFoundError as error:
            raise IncompleteAgentDefinition(
                f"Agent { definition.name!r } is missing a prompt template: { error }"
            ) from error

        return AgentRole(
            name=definition.name,
            output_path=configuration.output_path,
            system_prompt=system_prompt,
            task_prompt=task_prompt,
            allowed_tools=list( configuration.allowed_tools ),
            max_turns=configuration.max_turns,
        )
