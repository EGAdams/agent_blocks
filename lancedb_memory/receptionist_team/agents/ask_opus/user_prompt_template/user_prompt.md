The user wants you to ask Opus something. Their words are in the additional
instructions below. If there is no actual question there, ask what they want
to ask Opus, and stop.

1. Split what you were given into:
   * the question, in the user's own words, and
   * the context: what the user said about it (the bug, what we were doing).
   If the user says "follow up", "also ask", or "ask Opus again", it is a
   follow-up to the last question: add --follow-up.

2. Run this with the Bash tool, timeout 600000 (Opus can take a few minutes):

   cd /home/adamsl/rol_finances && \
     PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH" uv run adws/adw_ask_opus.py \
     "<the question>" --context "<the context>" [--follow-up] \
     --message-out /tmp/ask_opus_line.txt

   Quote both exactly; escape any double quotes inside them.

3. Read /tmp/ask_opus_line.txt and reply with that line exactly as written.
   It already names Opus and the run it came from. If the file is missing or
   the command failed, say plainly that Opus couldn't be reached and give
   the last error line -- never answer from your own knowledge instead.
