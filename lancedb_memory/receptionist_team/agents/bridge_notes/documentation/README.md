# bridge_notes

Toyota's Groq specialist for the Bridge agent `notes`. The worker side is `agent_bridge_worker/notes_agent.py`.

- Diagnostic: `status`, `marker_write`, `marker_clear` touch only the `<date>_bridge-notes-diagnostic.md` note.
- Approved notes: `list`, `read` (no confirmation) and `update` (read-back and a spoken "yes" first), by file name. The server's approved and protected lists decide which notes; Toyota cannot widen them.
- An update names the revision Toyota last read in this process (`desk.NOTE_REVISIONS`), never one the model wrote. Without a read first she asks for one. The worker refuses the write if the note has moved, or if any `find` text is not present exactly once; after a write it reads the note back twice before reporting success.

See `receptionist_team/bridge_client/README.md` (Bridge specialists).
