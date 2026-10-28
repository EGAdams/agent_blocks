The user's request is in the additional instructions below. Send it through
the post office with:

    cd /home/adamsl/agent_blocks && lancedb_memory/.venv/bin/python adws/adw_op_delegate_task.py \
      --sender toyota --text "<the request, naming the agent>" --message-out /tmp/post_office_line.txt

Reply with the line in /tmp/post_office_line.txt exactly as written. For
"what's the status", run adws/adw_op_task_status.py --sender toyota the same way.
