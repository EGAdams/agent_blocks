The user wants a systemd service started, stopped, restarted, reloaded,
enabled, disabled, or checked. Which service, and which of those actions, are
in the additional instructions below, in the user's own words. If either the
service name or the action is not clear from what you were given, do not
guess -- ask which service and which action, and stop.

1. Decide the arguments:
   - `--service`: the exact systemd unit named or clearly implied (e.g.
     "toyota-receptionist", "nginx.service"). Never invent a service name
     that was not given or obviously implied.
   - `--action`: map the user's words to exactly one of start, stop, restart,
     reload, reload-or-restart, try-restart, enable, disable, status,
     is-active. "restart" is the default reading of "restart it" / "bounce
     it" / "kick it"; "status" / "is it up" means status; never choose
     start/stop/restart when the user only asked to check something.
   - `--scope`: "user" for one of this box's own user-level services (the
     ones started as this user, including the receptionist sidecars);
     "system" (the default) only if the instructions say it is a system-wide
     service or name one you know runs under the system manager.
   - Add `--dry-run` only if the user explicitly asked to preview or asked
     "what would happen" without doing it.

2. Run, from /home/adamsl/agent_blocks:

   /home/adamsl/agent_blocks/lancedb_memory/.venv/bin/python \
     adws/adw_system_mechanic.py "<why this admin action is being run>" \
     --service "<the unit>" --action <the action> --scope <system|user> \
     [--dry-run] --message-out /tmp/system_mechanic_line.txt

   Quote the prompt and the service name exactly. Do not add any flag not
   listed here, and never substitute a different command for this one --
   this workflow is the only way you ever touch a service.

3. Read /tmp/system_mechanic_line.txt after the run and treat its one line as
   the ground truth of what happened -- do not add to it or soften a failure
   into a success.

4. Reply with exactly that one line (or a very close paraphrase of it if it
   reads awkwardly spoken aloud). If the run's exit status was non-zero,
   still report the line; it already explains what went wrong.
