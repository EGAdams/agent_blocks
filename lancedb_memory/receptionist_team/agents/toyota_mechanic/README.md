# toyota_mechanic -- definition NOT yet on disk

This folder is a placeholder. The mechanic's real `system_prompt` and
`task_prompt` still live in `finance_team/roles.py`, which is off-limits to
edits (see `receptionist_team/extra_roles.py`'s docstring for why), together
with `finance_team/mechanic_runner.py`.

So there is intentionally **no** `system_prompt_template/system_prompt.md`
here. Writing one would create a second copy of the prompt that nothing
renders, and the two would drift the first time either side changed.

To migrate it, in one commit:

1. copy `finance_team.roles.ROLES["toyota_mechanic"].system_prompt` into
   `system_prompt_template/system_prompt.md` and its `task_prompt` into
   `user_prompt_template/user_prompt.md`, verbatim;
2. put `output_path`, `max_turns` and `allowed_tools` into
   `model_confiurations/model.json`;
3. register it in `receptionist_team/extra_roles.py` the way
   `spreadsheet_reshaper` is, so the folder becomes the definition;
4. delete the entry from `finance_team/roles.py` -- which requires that file
   becoming editable first.

`.venv/bin/python -m receptionist_team.agents.cli validate` reports this
folder as incomplete until then. That is the correct signal, not a bug.
