"""Strategy registry: one validation policy per installed assignment.

Before this, ``WorkflowModel`` held a single ``AssignmentPolicy`` and the tax
policy refused anything that was not ``tax_issues`` -- so a second assignment
could not be validated at all, and the capability text that tells Toyota what
is installed was a hand-maintained string that nothing checked. Both facts now
come from the same registry, which is the only place an assignment is declared.
"""
from ..interfaces import AssignmentPolicy


class InstalledPolicies:
    def __init__(self, policies: dict[str, AssignmentPolicy]):
        if not policies:
            raise ValueError("At least one assignment policy must be installed.")
        self.policies = dict(policies)

    def installed(self) -> tuple[str, ...]:
        return tuple(sorted(self.policies))

    def for_assignment(self, name: str) -> AssignmentPolicy:
        policy = self.policies.get(name)
        if policy is None:
            # Name what is installed: a bare refusal leaves the user guessing which
            # words would have worked, and the model re-guessing on the next turn.
            raise ValueError(
                "That assignment is not installed. Installed: " + ", ".join(self.installed())
            )
        return policy
