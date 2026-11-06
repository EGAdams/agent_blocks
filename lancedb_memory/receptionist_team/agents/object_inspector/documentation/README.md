# object_inspector

Toyota's read-only window on the ADW object trace (`lancedb_memory/object_trace/`). Normally bypassed: `adapters/inspection_runner.ObjectInspectionRunner` calls `GET http://127.0.0.1:8790/api/ask/<question>?subject=` directly. That is the same service the debugger page at `/object-debugger.html` reads. `adapters/inspection_route.ObjectInspectionRoute` forces this route for:

| Words | Inspector question | Subject |
|---|---|---|
| "show the latest Notes update" | `latest_notes_update` | none |
| "where did task t_... stop?" | `where_stopped` | a task id, or the latest task |
| "show object agent.notes:t_...:1's state and error" | `object_state` | an object id |
| "did the content save, and did the browser render that version?" | `saved_and_rendered` | a task id, or the latest write |
