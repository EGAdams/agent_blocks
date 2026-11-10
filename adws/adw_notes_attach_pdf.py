#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "selenium"]
# ///
"""ADW notes.attach_pdf -- attach a PDF to a note (--pdf-file, --title, [--name], [--upload-sha256]); the note's text is untouched.

One operation of adw_notes_review.py on its own (same flags, same harness, own ADW id):

    cd /home/adamsl/agent_blocks && uv run adws/adw_notes_attach_pdf.py --note <file name> [flags]

Phases: code(notes_attach_pdf)
"""
import sys
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parent ))

from adw_notes_review import main

if __name__ == "__main__":
    sys.exit( main( sys.argv[ 1: ], fixed="notes.attach_pdf" ))
