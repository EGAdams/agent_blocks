The user wants an HTML report of every Gifts & Love Offerings expense in the
organization's live accounting records for one year, with source details on
each row and duplicate gifts flagged. What they said, in their own words, is
in the additional instructions below.

1. If a year was not named, ask which year before running anything. Do not
   assume the current year and do not assume 2025 just because it is the
   organization's most recently closed year -- ask.

2. Run, from /home/adamsl/agent_blocks:

   /home/adamsl/agent_blocks/lancedb_memory/.venv/bin/python \
     adws/adw_gifts_love_offerings_report.py "<why this report is being built, in a few words>" \
     --year <the single calendar year they named> \
     --org-type NONPROFIT \
     --message-out /tmp/gifts_report_line.txt

   Leave --category and --category-id out unless the user asked for
   something narrower than "Gifts and Love Offerings" -- the default already
   resolves to the right category and walks its whole subtree of named
   recipients, which is what "all" means here (see the ADW's own docstring
   for why: the parent category holds almost no rows itself).

   Pass --no-children only if the user explicitly asked for just the parent
   category's own rows, not the named recipients under it. That is a narrower
   report than "all Gifts and Love Offerings" and should not be the default
   interpretation of an ordinary request.

   If the user named a destination path for the file, pass it with --out;
   otherwise let the workflow choose its own reports folder.

3. If the run refuses because the category name matched more than one
   category, do not pick one. Report the candidates it listed, with their
   ids, and ask which.

4. Read /tmp/gifts_report_line.txt after the run and treat its one line as
   the ground truth. It names the category, the row count, the total, the
   path, and how many rows are worth a second look. Do not add a figure to
   it, do not round the figures in it, and do not describe the contents of a
   report you have not opened.

5. Reply with exactly that one line (or a very close paraphrase if it reads
   awkwardly spoken aloud -- but the numbers and the path stay character for
   character). If the exit status was non-zero, still report the line; it
   already explains what happened.

Never use --org-type for a set of books the user did not ask about. NONPROFIT
is the organization; this schema also holds two personal sets of books, and
handing over one of those as the organization's giving would be a real error
with real consequences.

Never pass --out to a path outside the workflow's own reports directory
unless the user named the destination themselves. Overwriting a file
somebody is working from is not recoverable from here.
