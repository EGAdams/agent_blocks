"""Run the mechanic's coding agent inside a sandbox: python -m finance_team.mechanic_runner <sandbox> <task.md>.
Protected from the mechanic itself (see adws/adw_modules/toyota_mechanic)."""
import asyncio
import sys
from pathlib import Path

from finance_team.adapters.tool_agent import run_role
from finance_team.roles import AgentRole

SYSTEM = """\
You are Toyota's mechanic. Toyota is the front-desk receptionist agent; her source
is the three packages receptionist_team, finance_team and agent_memory in your
current directory, which is a SANDBOX COPY. Edit only files under the current
directory; never touch absolute paths elsewhere, the database, or any service.
You may not change finance_team/roles.py or finance_team/mechanic_runner.py, and
you may not delete files. Work test-first: add or extend a test under
receptionist_team/tests/ (async test functions, the file ends with the repo's
`tests.runner.main` pattern; copy an existing test file), see it fail, then make it pass.
Run a suite with: PYTHONPATH=. /home/adamsl/agent_blocks/lancedb_memory/.venv/bin/python -m receptionist_team.tests.<module>
Do not claim a capability works unless a test proves it. Be small and specific; one
enhancement per run. When done, reply with: what you read, what you changed, and the test evidence."""


async def main(sandbox: str, task_file: str) -> None:
    role = AgentRole(name="mechanic_coder", output_path=sandbox, max_turns=60,
                     allowed_tools=["Read", "Edit", "Write", "Bash", "Grep", "Glob"],
                     system_prompt=SYSTEM, task_prompt=Path(task_file).read_text())
    result = await run_role(role, sandbox, "claude-sonnet-5", echo=False)
    print(result.final_text)
    sys.exit(1 if result.is_error else 0)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
