# Localizer Task

## Variables

### prompt

{{prompt}}

### previous_envelope

{{previous_envelope}}

### context_handoff_dir

{{context_handoff_dir}}

## Task

`prompt` names one image file's absolute path and the open question the human
wants shown on the page. Read that image with your `read` tool, find the
region that most likely answers the question, and emit your `Report` JSON.

## Report

Respond with ONLY valid JSON matching `LocalizationOutput` — no prose before or
after:

```json
{
  "status": "success",
  "summary": "<one sentence on what you looked for and where you found it>",
  "found": true,
  "x0": 0.52,
  "y0": 0.18,
  "x1": 0.78,
  "y1": 0.24,
  "reasoning": "The handwritten dollar amount sits on the center-right line of the check, just below the payee line.",
  "artifacts": [],
  "notes_for_next_agent": ""
}
```

A declared miss is the same shape with nothing invented in it:

```json
{
  "status": "success",
  "summary": "Could not find anything on this page resembling a check amount.",
  "found": false,
  "x0": 0.0,
  "y0": 0.0,
  "x1": 0.0,
  "y1": 0.0,
  "reasoning": "The image is a bank confirmation letter with no check image on it -- there is no check amount visible on this page.",
  "artifacts": [],
  "notes_for_next_agent": ""
}
```
