The user wants a site found by searching for it, and then opened in the real
Windows browser so they can log in manually. What they are looking for is in
the additional instructions below, in their own words. If they have not named a
site or anything to look up, do not guess one -- ask what to find, and stop.

1. Decide what goes in which argument:

   * The thing to search for goes in `--phrase`, in the user's own words --
     "Fifth Third Bank", not "fifth third bank login page official site". Do
     not add words they did not say.
   * If they gave you a search URL instead of a phrase -- anything like
     `https://www.google.com/search?q=Fifth+Third+Bank` -- put it in
     `--search-url` and leave `--phrase` off. The workflow reads the query out
     of it. Do not pass both.
   * If they named the domain they expect, pass it as `--prefer-domain`, e.g.
     `--prefer-domain 53.com`. Leave it off when they did not; the first
     organic result is usually right.
   * Only pass `--keep-path` if they asked for a specific page rather than the
     site's front door. By default the workflow trims to the front page, which
     is where a login lives.

2. Run, from /home/adamsl/agent_blocks:

   /home/adamsl/agent_blocks/lancedb_memory/.venv/bin/python \
     adws/adw_interface_search_open.py "<why this is being opened, in a few words>" \
     [--phrase "<what to search for>"] [--search-url "<the search URL>"] \
     [--prefer-domain "<expected domain>"] \
     --message-out /tmp/search_open_line.txt

   Quote the prompt and every value exactly as given. Do not add `--dry-run`;
   the user asked for the browser to actually open. Allow it about 30 seconds
   -- it starts a headless Chrome and loads a results page.

3. Read /tmp/search_open_line.txt after the run and treat its one line as the
   ground truth of what happened. Do not add to it, and do not soften a
   failure or a fallback into a success. Pay attention to which of these it
   says, because they are different outcomes:

   * it opened the site -- the normal case; say which URL.
   * it could not pick a result link and opened the SEARCH page instead -- say
     so plainly, so the user knows to click the row themselves.
   * no launcher was found, or nothing was searchable -- report the reason.

4. Reply with exactly that one line, or a very close paraphrase if it reads
   awkwardly spoken aloud. If the run's exit status was non-zero, still report
   the line; it already explains what happened.

5. If the user wanted a different site than the one that opened, do not
   re-run guessing. Tell them which URL opened and offer to try again with the
   domain they expect as `--prefer-domain`.
