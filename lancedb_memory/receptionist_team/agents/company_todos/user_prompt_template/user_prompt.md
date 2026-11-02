The user said something about the company to-do list. Their words are in the
additional instructions below.

Note first: in normal operation this folder is not what runs. Toyota reaches
the same workflow through CompanyTodoRunner, which spawns it directly and
repeats its line with no model in the path. These steps exist so that
swapping the runner for ToolAgentRunner is one line in profiles.py.

1. Run, from /home/adamsl/agent_blocks:

   /home/adamsl/agent_blocks/lancedb_memory/.venv/bin/python \
     adws/adw_interface_company_todos.py "<why, in a few words>" \
     --request "<the user's words, unchanged>" --by toyota \
     --message-out /tmp/company_todos_line.txt

   Pass the request through as the user phrased it. The workflow decides
   whether it is an add, a read-back or a check-off, and changes nothing when
   it cannot tell.

2. Read /tmp/company_todos_line.txt and reply with that line, word for word.

If the user's words are missing, ask what they want done with the list; never
guess an item.
