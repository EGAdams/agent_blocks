#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "selenium"]
# ///
"""ADW notes.mark_resolved -- remove an item's highlight with --evidence or --approval (--advance moves on).

One operation of adw_notes_review.py on its own (same flags, same harness, own ADW id):

    cd /home/adamsl/agent_blocks && uv run adws/adw_notes_mark_resolved.py --note <file name> [flags]

Phases: code(notes_mark_resolved)
"""
import sys
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parent ))

from adw_notes_review import main

if __name__ == "__main__":
    sys.exit( main( sys.argv[ 1: ], fixed="notes.mark_resolved" ))
