"""Cashy: a banking navigation agent that reads the page, never a picture of it.

Read DESIGN.md first. In one line: a person talks, `CashyAgent` (a Mediator)
interprets the sentence, lets the session's current State decide what it
means right now, and runs it -- a learned playback when one is verified
(Mode B), a step-by-step DOM exploration when not (Mode A) -- over the
DevTools-protocol browser in `cdp_browser`.

Where things are true:
  SQLite (click_workspace_sequences.db)   the executable sequences, their
                                           versions, runs, sessions, downloads
  LanceDB (adw_data/cashy/lancedb)        what Cashy learned, as a pointer to
                                           a registered version -- never a step
  nothing                                  passwords, MFA codes, cookies, balances
"""
