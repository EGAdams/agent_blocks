# Judge Task

## Variables

### prompt

{{prompt}}

### previous_envelope

{{previous_envelope}}

### context_handoff_dir

{{context_handoff_dir}}

## Task

`prompt` holds one expense, the categories its vendor is permitted to file
under, and the category it is currently filed under. Decide whether the
expense's own words support that category, then emit your `Report` JSON.

Two answers are valid and nothing else is:

* **Confirm** — the evidence names a purchase that plainly belongs in the
  assigned category. Set `abstained` to `false`, set `category_id` to the
  assigned category (not a different one), and quote the deciding words in
  `reading`.
* **Abstain** — anything else. Set `abstained` to `true`, leave `category_id`
  as `null`, and say in `summary` what you could not read.

There is nothing in `context_handoff_dir` to probe and no file to open. You
have no tools; answer from `prompt`.

## Report

Respond with ONLY valid JSON matching `CategoryOpinion` — no prose before or
after:

```json
{
  "status": "success",
  "summary": "The description names a plumbing part bought for the church sink, which is building upkeep.",
  "category_id": 134,
  "abstained": false,
  "reading": "plumbing wrench for church sink",
  "artifacts": [],
  "notes_for_next_agent": ""
}
```

An abstention is the same shape with nothing invented in it:

```json
{
  "status": "success",
  "summary": "The description is a merchant name and a store number only, so nothing here says what was bought.",
  "category_id": null,
  "abstained": true,
  "reading": "",
  "artifacts": [],
  "notes_for_next_agent": ""
}
```
