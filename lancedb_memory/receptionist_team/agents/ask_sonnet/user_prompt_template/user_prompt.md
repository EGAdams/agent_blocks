The user wants you to ask Sonnet something. Their words are in the additional
instructions below. If there is no actual question there, ask what they want
to ask Sonnet, and stop.

1. Split what you were given into:
   * the question, in the user's own words, and
   * the context: what the user said about it (the bug, what we were doing).
   If the user says "follow up", "also ask", or "ask Sonnet again", it is a
   follow-up to the last question: add --follow-up.

2. Run this with the Bash tool, timeout 600000 (Sonnet can take under a minute or two):

   cd /home/adamsl/rol_finances && \
     PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH" uv run adws/adw_ask_sonnet.py \
     "<the question>" --context "<the context>" [--follow-up] \
     --message-out /tmp/ask_sonnet_line.txt

   Quote both exactly; escape any double quotes inside them.

3. Read /tmp/ask_sonnet_line.txt and reply with that line exactly as written.
   It already names Sonnet and the run it came from. If the file is missing or
   the command failed, say plainly that Sonnet couldn't be reached and give
   the last error line -- never answer from your own knowledge instead.
