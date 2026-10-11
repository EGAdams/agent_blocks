# Locator Task

## Variables

### prompt

{{prompt}}

### previous_envelope

{{previous_envelope}}

### context_handoff_dir

{{context_handoff_dir}}

## Task

Read the destination out of what `prompt` gives you — the expense's own words, the
link that was rejected and why, and a receipt path if one was supplied. Build one
Google Maps search URL for it, then emit your `Report` JSON.

The link you return is checked with the same predicate the stored link failed: it
must be `http`/`https` with a host. Build it in the path form described in your
instructions, with no query string. A reply with an address but no link fails
validation — and so does a link built out of a reference number. If all you have
is codes, answer `"locatable": false` with an empty `map_link`, keeping
`"status": "success"`; that is accepted.

## Report

Respond with ONLY valid JSON matching `MapLinkOutput` — no prose before or after:

```json
{
  "status": "success",
  "summary": "<one sentence on what you read and where it came from>",
  "address": "1250 28th St SW, Wyoming, MI",
  "map_link": "https://www.google.com/maps/search/1250+28th+St+SW+Wyoming+MI",
  "confidence": "medium",
  "locatable": true,
  "artifacts": [],
  "notes_for_next_agent": ""
}
```

A declared miss is the same shape with nothing invented in it:

```json
{
  "status": "success",
  "summary": "The expense carries only a reference number (REF:88213) and no receipt was supplied.",
  "address": "",
  "map_link": "",
  "confidence": "low",
  "locatable": false,
  "artifacts": [],
  "notes_for_next_agent": ""
}
```
