# scala_questions

How EG answers Scala when she can't tell where a scan goes. Added 2026-10-31
("If Scala doesn't know where to file something can she ask me to give her a
new folder ... so she can make and remember it?").

## The loop

1. `adw_interface_scala_file_scan.py` leaves an unsure scan in
   `scanned_documents/inbox` and, in its `ask` phase, adds a company to-do item
   by "scala": "#7 Scala needs a folder for ...".
2. EG tells Toyota: "Scala, file #7 under documents_511/insurance" (or "a new
   bills folder called water"). `adapters/scala_route.ScalaAnswerRoute` forces
   this agent, ahead of the to-do route; `ScalaAnswerRunner` runs
   `adw_interface_scala_answer.py` and repeats its line.
3. The scan is filed (folder made if new), its inbox copy removed, the to-do
   checked off, and a lesson written to `~/scala/lessons.jsonl`. Haiku reads
   every lesson on later scans, so the next page like it goes there itself.

"What is Scala waiting on?" lists the open questions and files nothing.

Logic: `lancedb_memory/scala/questions.py`. Tests: `.venv/bin/python -m
scala.tests.test_scala` and `receptionist_team/tests/test_scala_answer_route.py`.

Diagrams and an HTML manual are a separate job for a separate agent.
