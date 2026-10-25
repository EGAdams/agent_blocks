Toyota wants her own code diagnosed or improved. The request is in the
additional instructions below. Run this from `/home/adamsl/agent_blocks`, quoting the request:

    cd /home/adamsl/agent_blocks && lancedb_memory/.venv/bin/python adws/adw_toyota_mechanic.py "<request>" [--diagnose]

Add --diagnose when the user only wants to know what is wrong or what comes next
(nothing is changed). Without it, the mechanic makes the change in a sandbox,
runs every test, and applies it automatically only if all tests pass. This can
take several minutes; wait for it to finish.

Read the last lines of its output:
- `DIAGNOSIS:` -> relay it in a few plain sentences. Nothing was changed.
- `DONE:` with `CHANGED:` lines -> say which files changed and that the tests
  passed, then that Toyota restarts about 30 seconds after this reply (she may
  be offline about 30 seconds more on top of that) and rolls back by herself
  if she does not come back healthy.
- `NO_CHANGE:` -> the mechanic changed nothing; relay why.
- `REFUSED:` -> nothing reached Toyota's live code. Say why in plain words.
- `BUSY:` -> another mechanic run is in progress; ask them to try again later.
- Anything else or a crash -> say it failed and quote the last error line.
The mechanic only works on Toyota's own Python. Never claim a change works
beyond what the tests proved.
