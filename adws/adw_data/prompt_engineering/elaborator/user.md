# Elaborator Task

## Variables

### prompt

{{prompt}}

### previous_envelope

{{previous_envelope}}

### context_handoff_dir

{{context_handoff_dir}}

## Task

`prompt` gives you the folder's survey facts, the `needed_item`/`reread_reason`
it came from, and the one `open_question` the human wants explained in plain
language. Emit your `Report` JSON.

## Report

Respond with ONLY valid JSON matching `ElaborationOutput` — no prose before or
after:

```json
{
  "status": "success",
  "summary": "<one sentence on what this question is really asking>",
  "explanation": "The scan shows a check number and date, but not the dollar amount written on the check itself -- that's a different part of the check than what got photographed or read. Without it, nobody can confirm how much was actually paid.",
  "suggested_step": "Look at the handwritten or printed amount line on the check image (usually center-right) and read off the dollar figure.",
  "artifacts": [],
  "notes_for_next_agent": ""
}
```
