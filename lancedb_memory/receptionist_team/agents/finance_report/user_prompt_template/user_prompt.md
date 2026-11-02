1. Run adws/adw_interface_finance_report.py from the agent_blocks root with
   the user's request as its prompt and --message-out pointing to a temporary file.
2. Read that file and return its result exactly. If the workflow fails, report
   its error and never claim a report was published. Only 2025 is supported;
   ask for the reporting year if the user requests a different year.
