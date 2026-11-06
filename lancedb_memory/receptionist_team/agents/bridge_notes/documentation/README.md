# bridge_notes

Toyota's Groq specialist for the Bridge agent `notes` (the Notes diagnostic). The worker side is `agent_bridge_worker/notes_agent.py`; it writes only its own `<date>_bridge-notes-diagnostic.md` note and returns revisions and marker sightings, never note text. See `receptionist_team/bridge_client/README.md` (Bridge specialists).
