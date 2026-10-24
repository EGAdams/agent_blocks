# documentation -- generated later, by a documentation agent

Empty on purpose. The subdirectories below name what goes where:

    mermaid diagrams/flowchart/   how a turn moves through this agent
    mermaid diagrams/sequence/    the call order across collaborators
    mermaid diagrams/class/       the interfaces this agent is built from
    html_manual/toc/              table of contents for the manual
    html_manual/public_links/     links safe to publish outside the box

Generating these is a separate job from loading an agent: it means reading
the agent's prompts, tool declarations and model config and emitting
diagrams plus an HTML manual, and it must not run on every catalog build.
`documentation.py` therefore only *reports* which of these directories have
files in them -- see its module docstring.

`.venv/bin/python -m receptionist_team.agents.cli show <agent>` prints the
written/empty counts.
