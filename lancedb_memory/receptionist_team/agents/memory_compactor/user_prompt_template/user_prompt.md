The user wants the shared LanceDB memory store compacted -- old conversation
turns pruned and the freed space reclaimed, never the whole table cleared.
What to do is in the additional instructions below, in the user's own words.

1. Decide the retention window: how many days of turns to KEEP. If the
   instructions give a number (or something you can turn into one, like "a
   month" or "90 days"), use it. If they say nothing about how far back to
   go, do not invent a number -- run the ADW's own default by omitting
   --older-than-days, and say in your reply that you used the default.

2. Decide whether this is a preview or a real run. Only pass --dry-run when
   the user asked to see what would happen, asked to "check" or "preview",
   or did not clearly ask for the deletion to actually happen. Run for real
   (no --dry-run) when the user clearly asked to compact, clean up, or free
   space now.

3. Decide --table: if the user named one table, pass it; otherwise omit
   --table so every table at the default LanceDB location is covered.

4. Run, from /home/adamsl/agent_blocks:

   /home/adamsl/agent_blocks/lancedb_memory/.venv/bin/python \
     adws/adw_memory_compact.py "<why this compaction is being run>" \
     [--older-than-days <N>] [--table <name>] [--dry-run] \
     --message-out /tmp/memory_compact_line.txt

5. Read /tmp/memory_compact_line.txt after the run and treat its one line as
   the ground truth of what happened -- do not add to it, soften a refusal
   into a success, or claim rows were removed that the line does not report.

6. Reply with exactly that one line (or a very close paraphrase of it if it
   reads awkwardly spoken aloud). If the script refused to touch a table
   because the cutoff would have emptied it, say so and name the table --
   that is the safety behaving correctly, not a failure to explain away.
