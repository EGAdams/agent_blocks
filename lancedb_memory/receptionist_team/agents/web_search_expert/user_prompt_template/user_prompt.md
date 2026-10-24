The user wants something found on the web. What they're looking for, in their
own words, is in the additional instructions below. If they gave nothing to
search for, do not guess -- ask what to look for, and stop.

1. Build the actual search query:

   * Start from the user's own words.
   * If something in their phrasing is almost certainly a mis-hearing or
     mis-typing of a specific proper noun you can infer from the
     conversation (e.g. "Lens database" when the conversation has been about
     the "Lance database"), use the corrected term. Only correct what you can
     justify from context; do not rewrite phrasing that is merely informal.
   * If two real meanings are both plausible and nothing in the conversation
     favors one, ask which they meant instead of picking for them.
   * Do not add qualifiers the user did not ask for -- site filters, dates,
     "official site", and similar -- unless they said so.

2. Run, from /home/adamsl/agent_blocks:

   /home/adamsl/agent_blocks/lancedb_memory/.venv/bin/python \
     adws/adw_web_search.py "<why this search is being run, a few words>" \
     --query "<the corrected search text>" \
     [--num 5] [--provider serpapi|google_cse|brave] \
     --results-out /tmp/web_search_results.json

   Leave --provider off so it walks the SERP API / Google / Brave ladder
   itself; only pass it if the user specifically asked for one engine. Quote
   the prompt and --query exactly as built in step 1.

3. Read /tmp/web_search_results.json after the run. Its "succeeded" field and
   "message" are the ground truth; its "hits" list (title/link/snippet) is
   the only source of results you may report. If "succeeded" is false, say so
   plainly and give the "message" as the reason -- do not supply an answer
   from your own knowledge instead.

4. Reply by naming the corrected query you actually searched for, if it
   differs from what the user said, then summarize the top results in plain
   language -- what each is and its link. Keep it to what fits in a few
   spoken sentences; do not read out every snippet verbatim.

5. If the workflow found nothing or failed, report that plainly and suggest
   one adjustment -- a different phrase, or confirming which provider -- 
   rather than guessing an answer.
