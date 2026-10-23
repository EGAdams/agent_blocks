The user wants a URL, or a search phrase, opened in the Windows browser on
this box. What to open is in the additional instructions below, in the
user's own words. If neither a URL nor a search phrase is given, do not guess
one -- ask what to open, and stop.

1. Decide which argument the instructions give you: a literal web address
   (has a scheme or an obvious domain, e.g. "example.com" or
   "https://example.com") goes in --url; anything else -- a place, a
   question, a thing to look up -- goes in --phrase. Pass only one of them
   unless the instructions clearly give you both a fallback phrase and a URL
   to try first.

2. Run, from /home/adamsl/agent_blocks:

   /home/adamsl/agent_blocks/lancedb_memory/.venv/bin/python \
     adws/adw_browser_open.py "<why this is being opened, in a few words>" \
     [--url "<the literal URL>"] [--phrase "<the search phrase>"] \
     --message-out /tmp/browser_open_line.txt

   Quote the prompt and any --url/--phrase value exactly as given. Do not add
   --dry-run; the user asked for the browser to actually open.

3. Read /tmp/browser_open_line.txt after the run and treat its one line as
   the ground truth of what happened -- do not add to it or soften a failure
   into a success.

4. Reply with exactly that one line (or a very close paraphrase of it if it
   reads awkwardly spoken aloud). If the run's exit status was non-zero,
   still report the line; it already explains why nothing opened.
