# Intake Task

## Variables

### prompt

{{prompt}}

### previous_envelope

{{previous_envelope}}

### context_handoff_dir

{{context_handoff_dir}}

## Task

`prompt` names the folder and gives you every extraction file already read for it
(and a `concerns.md`, when one exists). Decide what, if anything, is still needed
to complete that form, then emit your `Report` JSON.

## Report

Respond with ONLY valid JSON matching `FormIntakeOutput` — no prose before or after:

```json
{
  "status": "success",
  "summary": "<one sentence on what this form is and whether it is complete>",
  "form_name": "Mortgage Interest Statement (Form 1098)",
  "complete": false,
  "needed_items": [
    "Box 1 mortgage interest received is present, but the lender's TIN is blank"
  ],
  "open_questions": [
    "What is the lender's TIN shown on the original 1098 — is it legible on the scan?"
  ],
  "reread_recommended": false,
  "reread_reason": "",
  "artifacts": [],
  "notes_for_next_agent": ""
}
```

A fully complete form reports no items and no questions:

```json
{
  "status": "success",
  "summary": "Form 1098 extraction already carries every box needed to file.",
  "form_name": "Mortgage Interest Statement (Form 1098)",
  "complete": true,
  "needed_items": [],
  "open_questions": [],
  "reread_recommended": false,
  "reread_reason": "",
  "artifacts": [],
  "notes_for_next_agent": ""
}
```
