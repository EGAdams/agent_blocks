Build request from {sender} (task {task_id}).

Repository: {workdir}
Context: {context}

Request, in the user's words:
{request}

1. Read the repository's CLAUDE.md and the code the request touches.
2. Make the change. Prefer small modules and program to interfaces, matching
   what is already there.
3. Run the relevant tests and fix what you broke.
4. Write a report to {output}: what you changed (files), why, and the exact
   test commands and results.
5. Reply with three or four plain sentences: what was built, where, and
   whether the tests passed.
