#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# ///
"""RETIRED 2026-11-09 -- use adw_notes_watch.py (notes.watch).

This watcher opened the dashboard in its own throwaway headless Chrome and read highlights off that copy of the
page. It was merged into notes_review (see notes_review/DESIGN.md), where watching reads the saved note by the
dashboard's own rules and, with --page, confirms on the real dashboard tab through browser.notes_state:

    uv run adws/adw_notes_watch.py --note <file name> --until clear [--item-id ID] [--timeout 600] [--page]
    uv run adws/adw_notes_watch.py --note <file name> --until change
    uv run adws/adw_notes_watch.py --note <file name> --until active_change

Its last full version is in git history (a91df63).
"""
import sys

if __name__ == "__main__":
    sys.exit( __doc__ )
