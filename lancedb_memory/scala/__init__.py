"""scala -- the scanner clerk for everything that is not Mazda's.

Mazda files receipts and statements. Scala files the rest of what comes off
the Window and Freezer scanners -- household bills, probate letters, other
documents -- into `C:\\Users\\NewUser\\scanned_documents` on this box:

    watch.py          new staged scans in incoming_scans/ -> one ADW run each
    desk.py           the policy: duplicate? Mazda's? rules >= 0.90? Haiku? inbox
    rules.py          OCR words against a table of document kinds (no model)
    haiku.py          the fallback, Claude Haiku via the Agent SDK
    cabinet.py        the folder tree, grown only under bills_511/ and documents_511/
    ledger.py         ~/scala/ledger.jsonl, one line per scan
    mazda_verdicts.py what Mazda's classifier said, read-only from the dashboard

The ADW is `adws/adw_interface_scala_file_scan.py`; its composition root is
the only place the concretes are named together. Haiku's brief is the agent
folder `receptionist_team/agents/scala/`.
"""
