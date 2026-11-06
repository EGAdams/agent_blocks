Use the Star Trek Bridge connector to inspect live ADW object and task
lifecycle events on my machine.

First call `list_available_agents` and confirm `object-debugger` appears.
Then call `submit_agent_task` with:

```json
{
  "agent": "object-debugger",
  "workspace": "object_trace",
  "operation": "inspect",
  "prompt": "status"
}
```

Keep those first three fields the same for every debugger request. Change
only the prompt:

- `status`: source availability, freshness, object/event counts, and latest sequence.
- `recent 10`: newest recorded lifecycle events; maximum 200.
- `task t_d496bd4b1819fb4bea696e97`: phase outcomes, state, and evidence for this example task. Substitute another actual bridge task ID as needed.

Requests run asynchronously. Save the returned `task_id`, poll
`get_agent_task_status`, then call `get_agent_task_result` when finished.
If queued or running, keep following that same ID. Do not submit duplicate
tasks to check progress. If you supply an `idempotency_key`, use a new one
for each fresh inspection and reuse it only to retry the same submission.

Read the result body, including its JSON details. Report the observed time,
state, evidence, and any missing information. This agent is read-only;
it cannot change files, run shell commands, or repair anything.

Start by checking `status`, then `recent 10`, and summarize the results.
