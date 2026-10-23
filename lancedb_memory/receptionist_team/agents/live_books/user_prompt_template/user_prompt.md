The user wants a figure out of the organization's live accounting records.
Their question is in the additional instructions below, in their own words.

Note first: in normal operation this agent folder is not what runs. Toyota
reaches the same workflow through AdwLiveFinanceRunner, which spawns it
directly and repeats its line with no model in the path -- the cheapest and
least corruptible route to a number. These steps exist for the case where a
question needs looking around first (checking which questions are installed,
trying a year, reconciling two answers), and so that swapping the runner for
ToolAgentRunner is a one-line change in profiles.py rather than a new folder.

1. If you are unsure the question is one the workflow installs, list them
   first -- this touches no database and costs nothing:

   /home/adamsl/agent_blocks/lancedb_memory/.venv/bin/python \
     adws/adw_interface_live_finance.py --list-questions

2. Run, from /home/adamsl/agent_blocks:

   /home/adamsl/agent_blocks/lancedb_memory/.venv/bin/python \
     adws/adw_interface_live_finance.py "<why this is being asked, in a few words>" \
     --question "<the user's question, in their own words>" \
     --org-type NONPROFIT \
     [--year <a single calendar year, only if they named one>] \
     [--limit <rows a grouped answer may list, default 10>] \
     --message-out /tmp/live_finance_line.txt

   Pass the question through as the user phrased it. Do not rewrite it into
   what you think they meant -- the workflow's own matcher decides which
   installed question it is, and a rewrite is where a different question gets
   answered. Give --year only when they named exactly one year.

3. Read /tmp/live_finance_line.txt after the run and treat its one line as the
   ground truth. Do not add a figure to it, do not round the figures in it,
   and do not turn "no installed question matched" into an answer.

4. Reply with exactly that one line (or a very close paraphrase if it reads
   awkwardly spoken aloud -- but the numbers themselves stay character for
   character). If the exit status was non-zero, still report the line; it
   already explains what happened.

Never use --org-type for a set of books the user did not ask about. NONPROFIT
is the organization; this schema also holds two personal sets of books, and
reporting one of those as the organization's spending would be a real error
with real consequences.
