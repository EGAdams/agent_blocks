The user has a question about, or a wanted change to, the Letta-Code
Dashboard codebase at /home/adamsl/letta-code/dashboard/. What they want is in
the additional instructions below, in their own words. If the instructions
don't say which file or feature they mean, and the codebase has more than one
plausible match, name the candidates and ask instead of guessing.

1. Read what you need from /home/adamsl/letta-code/dashboard/ to answer or
   make the change -- start with CLAUDE.md and AGENTS.md for orientation,
   then the specific file(s) the question or change concerns (commonly
   server.py, dashboard.html, a file under js/ or css/, or a test under
   tests/).
2. If the instructions ask a question, answer it directly, citing the file
   and, where useful, the function or section you read it from.
3. If the instructions ask for a code change, make it with Edit (or Write for
   a brand-new file), staying inside /home/adamsl/letta-code/dashboard/. Run
   the relevant part of the existing test suite before calling the change
   done (.venv/bin/python -m pytest tests/ from that directory), and say in
   your reply whether it passed.
4. A change to server.py only takes effect on the live dashboard after
   `systemctl --user restart dashboard-server.service` -- do not run that
   yourself. Tell the user the restart is needed and wait for their
   confirmation before running it.
5. Never edit anything under /home/adamsl/rol_finances, and never touch any
   database; both are outside this job.
6. Reply with one or two sentences: what you found or changed, which file(s),
   and whether tests passed (or that a restart is pending the user's
   confirmation).

If the file you need cannot be found or read, say so plainly and do not
write anything.
