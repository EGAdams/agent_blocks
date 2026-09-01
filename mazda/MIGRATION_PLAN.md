# Mazda Agent Blocks Migration Plan

## Purpose

This plan moves Mazda's reusable building blocks into:

```text
/home/adamsl/agent_blocks/mazda/
```

The move must happen **one working piece at a time**.

We are not trying to move every finance tool or every dashboard function into Agent Blocks.

The goal is to separate the system into three clear areas:

```text
letta-code
    receives documents and runs the dashboard

Agent Blocks
    contains Mazda's reusable thinking, coordination, models, and interfaces

rol_finances
    contains the finance tools that do the actual finance work
```

The most important rule is:

> **Do not move a piece until we understand what it does, where it lives now, who calls it, and what calls it next.**

---

# 1. Simple Mental Model

Think about the system like this:

```text
Document
   │
   ▼
letta-code
receives it
   │
   ▼
Mazda
figures out what work is needed
   │
   ▼
rol_finances
does the finance-specific work
   │
   ▼
Mazda
checks and organizes the result
   │
   ▼
letta-code
shows and saves the result
```

That is the overall direction.

The real system has more pieces than this, so the rest of this document maps those pieces.

---

# 2. What Happens When a Document Is Processed

The document-processing path appears to contain these stages:

```text
1. Receive the document
        │
        ▼
2. Identify what kind of document it is
        │
        ▼
3. Hand it to Mazda
        │
        ▼
4. Mazda routes the work
        │
        ▼
5. Parse the document
        │
        ▼
6. Identify the vendor
        │
        ▼
7. Link the receipt or transaction
        │
        ▼
8. Categorize the expense
        │
        ▼
9. Check for duplicates
        │
        ▼
10. Verify the result
        │
        ▼
11. Store the transaction
        │
        ▼
12. Build or update the Recent Report
```

This flow should be treated as the center of the migration.

Before moving any major Mazda object, the on-machine agent should verify this chain against the current code.

---

# 3. Known Document Entry Points

These pieces appear to be involved before Mazda starts doing finance work.

## `process_scanned_document()`

This appears to be one of the main entry points for scanned documents.

Its job is likely near the beginning of this flow:

```text
Scan arrives
    │
    ▼
process_scanned_document()
    │
    ▼
prepare / inspect / classify
    │
    ▼
send to Mazda
```

### Migration decision

Keep the scan entry point in `letta-code` if it exists mainly because of the dashboard or scanner.

Do not move it into Agent Blocks unless inspection shows that it is reusable Mazda behavior rather than application-specific document intake.

---

## `classify_scan.py`

This appears to help answer:

```text
"What kind of document did we receive?"
```

Examples may include:

```text
Receipt
Bank statement
Loose statement
Other document
```

### Migration decision

Do not move this automatically.

First determine whether it is:

```text
A. general Mazda document identification
or
B. finance-specific scan identification
```

If it is general Mazda behavior, it may belong in Agent Blocks.

If it is tightly tied to finance document types and finance tools, it may stay in `rol_finances`.

---

## `_notify_mazda_of_scan(...)`

This appears to be one of the handoff points between document intake and Mazda.

Conceptually:

```text
scan intake
    │
    ▼
_notify_mazda_of_scan(...)
    │
    ▼
Mazda
```

### Migration decision

The dashboard-specific caller stays in `letta-code`.

The reusable Mazda-facing request model may move into Agent Blocks.

---

## `_notify_mazda_of_pdf(...)`

This appears to perform a similar job for PDFs.

Conceptually:

```text
PDF intake
    │
    ▼
_notify_mazda_of_pdf(...)
    │
    ▼
Mazda
```

### Migration decision

Use the same rule as the scan path.

The PDF entry point can stay in `letta-code`, while reusable Mazda request objects may move into Agent Blocks.

---

# 4. Mazda's Main Worker Objects

Mazda appears to coordinate several smaller workers.

The known set is:

```text
Mazda
 │
 ├── Mazda Router
 ├── Mazda Parser
 ├── Mazda Vendor Identity
 ├── Mazda Receipt Linker
 └── Mazda Categorization
```

These should be investigated before moving a large object such as `FinanceOrchestrator`.

We need to understand whether `FinanceOrchestrator` sits above these workers, below them, beside them, or duplicates part of their job.

---

# 5. Mazda Router

The Router appears to decide where work goes next.

Conceptually:

```text
Document or task
      │
      ▼
 Mazda Router
      │
      ├── parsing work
      ├── vendor work
      ├── receipt-linking work
      ├── categorization work
      └── other finance work
```

### Likely Agent Blocks candidate

If the Router is reusable Mazda decision-making, it is a strong candidate for Agent Blocks.

Possible location:

```text
mazda/router/
```

Before moving it, verify:

- its current file;
- its public class name;
- who creates it;
- what objects it calls;
- what data it accepts;
- what data it returns;
- whether it directly talks to the database, filesystem, HTTP, or subprocesses.

Any direct application-specific work should remain outside the reusable block.

---

# 6. Mazda Parser

The Parser appears to turn a document into structured information.

For a receipt, the result may look conceptually like:

```python
{
    "date": "2026-08-29",
    "vendor": "Meijer",
    "amount": 29.48
}
```

The real model should come from the existing code, not from this example.

### Important ownership question

We need to determine whether `Mazda Parser`:

```text
A. actually parses documents itself
or
B. tells finance parsing tools to parse them
```

That difference matters.

If Mazda Parser mainly coordinates existing tools, the coordination can move into Agent Blocks while the actual receipt and statement parsing stays in `rol_finances`.

If Mazda Parser itself contains reusable parsing behavior, inspect it carefully before deciding.

Possible Agent Blocks location:

```text
mazda/parser/
```

---

# 7. Mazda Vendor Identity

This worker appears to answer questions such as:

```text
What vendor is this?
What vendor_key should be used?
Does this vendor already exist?
Should a new vendor key be created?
```

This connects to the existing vendor-key system.

Examples of vendor keys we have worked with include forms such as:

```text
cracker_barrel
meijer
amazon_marketplace
```

### Migration decision

The reusable decision-making and typed request/result models may belong in Agent Blocks.

The actual finance vendor data, YAML files, regex storage, and finance-specific lookup tools should remain with `rol_finances` unless there is a strong reason to move them.

Possible Agent Blocks location:

```text
mazda/vendor_identity/
```

---

# 8. Mazda Receipt Linker

This worker appears to connect receipt information to the finance transaction or statement information that belongs with it.

Conceptually:

```text
Receipt
   │
   ├──────────────┐
   │              │
   ▼              ▼
Receipt data   Bank transaction data
   │              │
   └──────┬───────┘
          ▼
   Receipt Linker
          │
          ▼
      Match result
```

### Migration decision

The reusable matching workflow may belong in Agent Blocks.

The actual finance searches and database access should remain in `rol_finances`.

Possible Agent Blocks location:

```text
mazda/receipt_linker/
```

---

# 9. Mazda Categorization

This worker appears to determine the finance category for an expense.

Conceptually:

```text
Transaction
    │
    ▼
Mazda Categorization
    │
    ▼
Suggested / selected category
```

### Migration decision

Separate the two concerns:

```text
Mazda decision-making
        versus
Finance category data and storage
```

Reusable Mazda decision-making may move into Agent Blocks.

Finance category definitions and finance storage should remain in `rol_finances`.

Possible Agent Blocks location:

```text
mazda/categorization/
```

---

# 10. Finance Work That Should Stay in `rol_finances`

The following work is finance-specific and should normally stay in:

```text
/home/adamsl/rol_finances/
```

This includes:

```text
Receipt parsing tools
Bank statement parsing tools
Vendor categorization data
Vendor-key storage
Regex matching
Duplicate searching
Transaction storage
MySQL access
Finance command-line tools
Finance report generation details
```

Mazda can ask these tools to do work without moving their source code into Agent Blocks.

A simple example:

```text
Mazda:
"Check whether this transaction already exists."

        │
        ▼

rol_finances:
performs the database search

        │
        ▼

Mazda:
receives True / False or a typed result
```

The database query stays with the finance system.

---

# 11. Intake Safety Pieces

Several lower-level pieces appear to protect document intake from duplicate or broken processing.

Known names include:

```text
_claim_scan_dispatch
_scan_content_sha256
_may_retry_terminal_scan
_observe_intake_callback
merge_recent_intake_status
```

These appear to deal with questions such as:

```text
Has this scan already been claimed?
Is this the same document we already processed?
Can this failed scan be tried again?
What happened during the callback?
What is the latest intake status?
```

Conceptually:

```text
Scan arrives
    │
    ▼
Has this scan already been claimed?
    │
    ├── YES → do not send it twice
    │
    └── NO
         │
         ▼
      continue
```

### Migration decision

Do not mix these pieces into receipt parsing or finance logic.

First determine whether they belong to:

```text
letta-code document intake
```

or whether some of them are reusable Mazda intake helpers.

If reusable, create a clearly named block such as:

```text
mazda/document_intake/
```

Avoid vague names.

---

# 12. Finance Verification Objects

The following names appear to be part of checking Mazda's work:

```text
MazdaEvaluationSuite
FinanceTraceFactory
StatementVerificationTask
VendorKeyTask
DuplicateDetectionTask
```

These should be mapped carefully because they may already represent useful block boundaries.

Possible structure:

```text
Document Verification
│
├── StatementVerificationTask
├── VendorKeyTask
├── DuplicateDetectionTask
└── MazdaEvaluationSuite
```

`FinanceTraceFactory` appears related to recording what Mazda did so that the work can be inspected later.

### Migration decision

Do not move these just because their names sound reusable.

For each object, verify:

- what it reads;
- what it writes;
- whether it talks directly to MySQL;
- whether it depends directly on the dashboard;
- whether it is a reusable Mazda object or finance-only code.

Reusable coordination and typed models may move.

Finance-specific data access stays in `rol_finances`.

---

# 13. Trainer Help Objects

The existing system also appears to contain objects for cases where Mazda cannot safely finish the work by herself.

Known names include:

```text
ProblemOnlyTrainerEscalationService
DetachedTrainerNotifier
ThreadingDeadlineScheduler
CallbackTrainerEscalationRecorder
TrainerLaunchRequest
IntakeCallback
recover_pending_trainer_watches
```

Some of these names should probably be simplified during the move.

For example, a clearer Agent Blocks area might be:

```text
mazda/trainer_help/
```

The important split is:

```text
Agent Blocks
    decides when Trainer help is needed
    and creates a typed request

letta-code
    handles application-specific process launching,
    background threads,
    dashboard updates,
    and process management
```

Do not move systemd or dashboard process-launch code into Agent Blocks.

---

# 14. Replace the Old Engine Names

The old names:

```text
runtime_kernel
runtime_family
```

are too vague for this project.

Use temporary working names that explain what the code actually does.

## Proposed name: `agent_engine_core`

Use this only if the code truly contains the reusable machinery that runs Mazda's agent behavior.

Possible location:

```text
mazda/agent_engine_core/
```

Think:

```text
Request
   │
   ▼
Agent Engine Core
   │
   ├── choose work
   ├── coordinate workers
   ├── pass typed data
   └── collect result
   │
   ▼
Result
```

## Proposed name: `agent_engine_types`

Use this only if the existing code really contains several related ways to build or run the agent engine.

Possible location:

```text
mazda/agent_engine_types/
```

### Important rule

Do not rename these areas until the on-machine agent finds the current source and explains what they actually do.

If the code shows that these names are still wrong, rename them again.

Clarity is more important than preserving an old diagram name.

---

# 15. Factories

The system appears to contain several factory-style objects.

Expected areas include:

```text
LLM Factory
Prompt Factory
Tool Factory
Context Factory
Workflow Factory
Evaluation Factory
Improvement Factory
Results / Reporting Factory
Agent Package Factory
```

A factory answers a question like:

```text
"What object should Mazda build for this job?"
```

Example:

```python
llm = llm_factory.create("haiku")
```

instead of scattering model-creation code throughout Mazda.

### Migration decision

Do not move the entire factory group at once.

For every factory:

1. find the real implementation;
2. identify what it creates;
3. identify what it depends on;
4. identify who calls it;
5. add focused tests;
6. move it only if it is reusable Mazda code.

If a factory exists only for one application's process launching, database setup, or dashboard behavior, leave it with that application.

---

# 16. Proposed Agent Blocks Shape

Do not create every directory on day one.

Create a directory when we have found the real object and are ready to move it.

A likely end state is:

```text
mazda/
├── README.md
├── MIGRATION_PLAN.md
│
├── interfaces/
├── models/
│
├── router/
├── parser/
├── vendor_identity/
├── receipt_linker/
├── categorization/
├── finance_orchestrator/
├── document_intake/
├── document_verification/
├── trainer_help/
│
├── agent_engine_core/
├── agent_engine_types/
│
├── llm_factory/
├── prompt_factory/
├── tool_factory/
├── context_factory/
├── workflow_factory/
├── evaluation_factory/
├── improvement_factory/
├── reporting_factory/
└── agent_package_factory/
```

This is a target map, not an instruction to create empty folders.

---

# 17. First Required Task: Build the Real Object Map

Before moving `FinanceOrchestrator`, the on-machine agent should map the actual document path.

Create a table like this:

| Step | Object / Function | Current File | Called By | Calls Next | Move? |
|---|---|---|---|---|---|
| Receive scan | `process_scanned_document()` | FIND | FIND | FIND | Probably stay |
| Classify | `classify_scan.py` | FIND | FIND | FIND | Decide |
| Hand scan to Mazda | `_notify_mazda_of_scan(...)` | FIND | FIND | FIND | Split if useful |
| Hand PDF to Mazda | `_notify_mazda_of_pdf(...)` | FIND | FIND | FIND | Split if useful |
| Route | Mazda Router | FIND | FIND | FIND | Likely move |
| Parse | Mazda Parser | FIND | FIND | FIND | Decide |
| Vendor | Mazda Vendor Identity | FIND | FIND | FIND | Likely split |
| Link receipt | Mazda Receipt Linker | FIND | FIND | FIND | Likely move |
| Categorize | Mazda Categorization | FIND | FIND | FIND | Likely split |
| Duplicate check | `DuplicateDetectionTask` | FIND | FIND | FIND | Decide |
| Verify statement | `StatementVerificationTask` | FIND | FIND | FIND | Decide |
| Vendor check | `VendorKeyTask` | FIND | FIND | FIND | Decide |
| Evaluation | `MazdaEvaluationSuite` | FIND | FIND | FIND | Decide |
| Trace | `FinanceTraceFactory` | FIND | FIND | FIND | Decide |
| Store | transaction storage | FIND | FIND | FIND | Stay |
| Recent Report | report persistence | FIND | FIND | FIND | Stay |

Do not proceed from guesses.

Fill this table from the actual source tree.

---

# 18. Second Required Task: Find `FinanceOrchestrator` in the Chain

The original plan said to move `finance_orchestrator` first.

That may still be correct, but we need to prove it.

The on-machine agent should answer:

```text
Where is FinanceOrchestrator created?

Who calls it?

What does it call?

Does it directly call:
- Mazda Router?
- Mazda Parser?
- Vendor Identity?
- Receipt Linker?
- Categorization?
- finance tools?
- database code?
- Trainer Help?
- Recent Report code?

Is it truly the main coordinator,
or is it only one worker in the larger chain?
```

Then draw:

```text
CALLER
  │
  ▼
FinanceOrchestrator
  │
  ├── ?
  ├── ?
  └── ?
```

Only after that should we decide whether it is Migration Step 1.

---

# 19. Migration Method for Every Block

For every object we move:

## Step A — Find the real source

Record:

```text
current file
current class/function
current tests
current callers
current dependencies
```

---

## Step B — Add or strengthen tests

Before moving the code:

```text
OLD LOCATION
     │
     ▼
focused tests
     │
     ▼
PASS
```

The tests should capture current behavior.

---

## Step C — Move one object

Example:

```text
rol_finances
     │
     ▼
agent_blocks/mazda/vendor_identity/
```

Move the reusable object only.

Do not drag unrelated database, dashboard, or process-launch code with it.

---

## Step D — Keep the old import working temporarily

Example:

```python
from agent_blocks.mazda.vendor_identity import VendorIdentity
```

The old location may temporarily re-export the moved class.

There should still be only one real implementation.

---

## Step E — Change callers

Move callers to the new import one at a time.

---

## Step F — Test the whole path

Test more than imports.

Run the real path:

```text
Scan / PDF
    │
    ▼
Mazda
    │
    ▼
Finance processing
    │
    ▼
Verified Transactions
    │
    ▼
Recent Report
```

---

## Step G — Remove the old import

Only remove the old path after every caller and deployed machine uses the new package successfully.

---

# 20. Recommended Migration Order

Do not lock this order until the object map is complete.

The current best working order is:

```text
0. Map the real document-processing chain

1. Confirm the real job of FinanceOrchestrator

2. Move the first small reusable Mazda coordinator
   - FinanceOrchestrator if the source map confirms it
   - otherwise start with the clearest worker

3. Mazda Router

4. Mazda Vendor Identity coordination

5. Mazda Receipt Linker

6. Mazda Categorization coordination

7. Intake Evidence / document verification pieces

8. Trainer Help decision-making

9. Agent Engine Core

10. Agent Engine Types

11. Factory group, one factory at a time

12. Remaining reusable document-dispatch pieces
```

The object map is more important than this numbering.

If the source says a different order is safer, follow the source.

---

# 21. What Must Stay in `letta-code`

Keep application-specific dashboard behavior in:

```text
/home/adamsl/letta-code/
```

Examples:

```text
HTTP routes
dashboard screens
scan entry points
PDF entry points
background threads
process startup and shutdown
systemd launching
Recent Report persistence if dashboard-specific
dashboard state
application-specific callbacks
```

Agent Blocks should not know how the dashboard server is started or how a systemd service is launched.

---

# 22. What Must Stay in `rol_finances`

Keep finance-specific machinery in:

```text
/home/adamsl/rol_finances/
```

Examples:

```text
receipt parser implementation
statement parser implementation
vendor YAML data
finance regex rules
duplicate database queries
transaction storage
MySQL
finance command-line tools
finance-specific report storage
```

Mazda can use these capabilities.

Using them does not mean their source code has to move into Agent Blocks.

---

# 23. What Should Move into Agent Blocks

Good Agent Blocks candidates are reusable Mazda objects such as:

```text
Pydantic request/result models
public interfaces
Mazda Router
reusable Mazda Parser coordination
Vendor Identity decision-making
Receipt Linker workflow
Categorization decision-making
Finance Orchestrator if confirmed
document evidence models
document verification coordination
Trainer Help decision-making
Agent Engine Core
Agent Engine Types
reusable factories
```

A simple test is:

> **Would this object still make sense as part of Mazda if the dashboard, MySQL setup, or finance file layout changed?**

If YES, it may belong in Agent Blocks.

If NO, it probably belongs in the application that depends on those details.

---

# 24. Pydantic Rule

Use Pydantic at important boundaries.

For example:

```python
class DocumentRequest(BaseModel):
    file_path: str
    document_type: str | None = None
```

```python
class ParsedExpense(BaseModel):
    vendor_key: str
    description: str
    date: str
    amount: Decimal
```

```python
class DuplicateCheckResult(BaseModel):
    is_duplicate: bool
    matching_transaction_id: str | None = None
```

The exact models should come from the working code.

Do not invent dozens of models simply for appearance.

Use them where they make data movement safer and easier to understand.

---

# 25. SPA Documentation Rule

The SPA must describe the real code.

Each Mazda page should answer:

```text
What does this object do?

Where is its source today?

Has it moved yet?

Who creates it?

Who calls it?

What does it call?

What data comes in?

What data goes out?

What tests cover it?

Is it real code or only a planned idea?
```

Do not mark a block complete because a documentation page exists.

A block is complete only when:

```text
real implementation exists
+
focused tests pass
+
the application uses it
+
the real Mazda document flow passes
```

---

# 26. Testing Checklist for Every Move

## Block test

Test the moved object by itself.

---

## Import test

During the transition, verify both old and new imports reach the same implementation.

---

## `agent_blocks` tests

Run the relevant Agent Blocks tests.

---

## `rol_finances` tests

Run the finance tests affected by the move.

---

## `letta-code` tests

Run the dashboard and document-intake tests affected by the move.

---

## SPA test

Verify:

```text
navigation
source display
Git status
diagrams
implementation status
```

---

## Real document test

Test at least one real receipt or scan through:

```text
document intake
    │
    ▼
Mazda
    │
    ▼
finance processing
    │
    ▼
Verified Transactions
    │
    ▼
Recent Report
```

Test the PDF path separately when the moved object is used there.

---

# 27. Safety Rules

## Never move the whole Mazda system at once

Small moves are easier to test and undo.

---

## Never keep two real implementations

Temporary old imports may point to the new object.

Do not copy the source and maintain two versions.

---

## Never move database code just because Mazda calls it

A Mazda worker can call a finance tool without owning its source.

---

## Never move dashboard process code into Agent Blocks

Dashboard threads, HTTP, systemd, and application process management stay in `letta-code`.

---

## Never create empty block directories just to match the SPA

Find the real code first.

---

## Never rename a major object before understanding its job

Names such as:

```text
agent_engine_core
agent_engine_types
```

are working names.

If the source shows a clearer name, use the clearer name.

---

## Keep every migration step reversible

Until the old import is intentionally removed, we should be able to put that one piece back without undoing the whole project.

---

# 28. Current Source Areas

## Dashboard and document entry points

```text
/home/adamsl/letta-code/dashboard/
```

Likely contains:

```text
scan entry points
PDF entry points
dashboard behavior
document intake helpers
Mazda notification functions
Recent Report behavior
Trainer process launching
```

---

## Mazda finance-agent code

```text
/home/adamsl/rol_finances/tools/self_improving_agent/agent_self_improvement/
```

Likely contains several current Mazda pieces, including some combination of:

```text
finance coordination
evidence handling
evaluation
factories
agent execution behavior
self-improvement behavior
```

---

## Finance tools

```text
/home/adamsl/rol_finances/tools/
```

Contains finance-specific processing such as:

```text
receipt parsing
statement parsing
categorization
duplicate checking
transaction storage
```

---

## Agent Blocks destination

```text
/home/adamsl/agent_blocks/mazda/
```

Reusable Mazda pieces move here one at a time.

---

## Mazda SPA documentation

```text
/home/adamsl/agent_blocks/spa_documentation/mazda/
```

This should explain the source that actually exists.

---

# 29. The Whole System in One Picture

```text
                         DOCUMENT
                            │
                            ▼
                  ┌──────────────────┐
                  │    letta-code    │
                  │                  │
                  │ scan / PDF entry │
                  │ document intake  │
                  └────────┬─────────┘
                           │
                           ▼
                  ┌──────────────────┐
                  │      Mazda       │
                  │                  │
                  │ Router           │
                  │ Parser           │
                  │ Vendor Identity  │
                  │ Receipt Linker   │
                  │ Categorization   │
                  └────────┬─────────┘
                           │
                 asks finance tools
                    to perform work
                           │
                           ▼
                  ┌──────────────────┐
                  │   rol_finances   │
                  │                  │
                  │ receipt parsing  │
                  │ statement parse  │
                  │ vendor data      │
                  │ duplicate check  │
                  │ MySQL / storage  │
                  └────────┬─────────┘
                           │
                           ▼
                  ┌──────────────────┐
                  │      Mazda       │
                  │                  │
                  │ verification     │
                  │ evaluation       │
                  │ trace / results  │
                  └────────┬─────────┘
                           │
                           ▼
                  ┌──────────────────┐
                  │    letta-code    │
                  │                  │
                  │ Recent Report    │
                  │ dashboard        │
                  └──────────────────┘
```

As reusable Mazda pieces are moved, the Mazda box gradually comes from:

```text
/home/adamsl/agent_blocks/mazda/
```

The dashboard remains in `letta-code`.

The finance toolbox remains in `rol_finances`.

---

# 30. Definition of Done

The migration is complete when:

- the real document-processing object map is documented;
- every reusable Mazda object has one clear home;
- reusable Mazda objects live under `/home/adamsl/agent_blocks/mazda/`;
- important boundaries use Pydantic models;
- each moved block has focused tests;
- `letta-code` uses Agent Blocks for reusable Mazda behavior;
- `rol_finances` uses Agent Blocks where appropriate;
- receipt and statement processing tools remain in `rol_finances`;
- database access remains in `rol_finances`;
- dashboard-specific code remains in `letta-code`;
- temporary old imports have been removed after every caller has moved;
- the SPA matches the working source;
- the real scan workflow works;
- the real PDF workflow works;
- Verified Transactions are correct;
- the Recent Report is correct.

---

# 31. Instructions for the On-Machine Agent

Before changing architecture, please do the following:

1. Find every object and function named in this document.
2. Correct any names that are stale or inaccurate.
3. Fill in the Document Processing Object Map with exact source files.
4. Trace the real call chain from scan/PDF intake through Recent Report.
5. Locate `FinanceOrchestrator` in that call chain.
6. Explain whether `FinanceOrchestrator` is truly the best first object to move.
7. Identify any major document-processing objects missing from this plan.
8. Identify any object listed here that no longer exists.
9. Recommend the safest first migration step based on the current source.
10. Do not move code until this map is verified.

The first deliverable should be the corrected object map and call chain, not a code move.
