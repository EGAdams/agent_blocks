EG said something to Scala about one of her filing questions. Their words are in the additional instructions below.

Note first: in normal operation this folder is not what runs. Toyota reaches the same workflow through ScalaAnswerRunner, which spawns it directly and repeats its line with no model in the path. These steps exist so that swapping the runner for ToolAgentRunner is one line in profiles.py.

1. Run, from /home/adamsl/agent_blocks:

   /home/adamsl/agent_blocks/lancedb_memory/.venv/bin/python \
     adws/adw_interface_scala_answer.py "<why, in a few words>" \
     --request "<EG's words, unchanged>" \
     --message-out /tmp/scala_answer_line.txt

2. Read /tmp/scala_answer_line.txt and reply with that line, word for word.

If EG's words are missing, ask which question and which folder; never guess either.
