# live_books

Toyota's one path to a real number.

## Why it exists

Before this, Toyota's only data-shaped assignment was synthetic: one training
expense, #900001, $12.34, in the `rol_receptionist_training` schema, and her
injected capability text announced "synthetic TRAINING environment" on every
turn. Asked what the organization had actually spent, she had no way to find
out, so she produced a believable figure instead. That reply was then stored,
recalled, and re-asserted; 66 training turns were in her LanceDB session when
this folder was written, and `receptionist_team/forget.py` was used to delete
them.

The memory was the symptom. The cause was that no number was reachable. This
is the number being reachable.

## What actually runs

`adws/adw_interface_live_finance.py`, against the live `nonprofit_finance`
schema, scoped to the organization whose `organizations.type` is `NONPROFIT`
(ROL) -- resolved by type each run, never by a pinned `org_id`, because the
same schema also holds two sets of personal books.

In normal operation **this folder's prompts are not used**. `profiles.py` maps
this agent to `AdwLiveFinanceRunner`, which spawns the ADW and returns its one
written line verbatim, with no model in the path. That is deliberate and it is
the whole safety argument: a figure that no model has rephrased is a figure
that cannot have drifted. The same deterministic-first order Mickey's tracker
uses.

The folder is here for two reasons. A question that needs looking around first
-- list the installed questions, try a year, reconcile two answers -- wants an
agent turn. And keeping the folder means swapping `AdwLiveFinanceRunner` for
`ToolAgentRunner` is one line in `profiles.py`, not a new folder plus a
registration.

## Read-only, twice

The MySQL account on this box holds ALL PRIVILEGES on the schema, so grants
are not the safeguard:

- `ReadOnlyBooks`, a Decorator over `ILiveBooks`, refuses any statement that is
  not a single SELECT -- no second statement, no write keyword. Every
  question's SQL passes through it.
- `PyMySQLLiveBooks` opens its session `READ ONLY` and never commits, so the
  server refuses a write that somehow got past the Decorator.

## The installed questions

A Strategy registry, `install_questions()`, most specific first. Adding one is
a single entry there: no phase changes, no new flag, nothing to edit here or
in the receptionist.

| question | answers |
|---|---|
| `missing_receipts` | expenses with no receipt recorded |
| `unverified` | how many are not yet marked human-verified |
| `spend_by_month` | spending totalled by month |
| `spend_by_category` | the largest categories by amount |
| `spend_by_method` | the cash / card / bank / other split |
| `top_vendors` | who was paid the most |
| `recent_expenses` | the most recently dated entries |
| `total_spend` | total, count, and the dates they span |

`--list-questions` prints them and touches no database.

When nothing matches, nothing is answered: the line names the installed
questions and says plainly that no figure was read. It never falls through to
a default query, because a number answering a question nobody asked is the
exact failure this was built to end -- the same rule `UnwiredRunner` enforces
for Mazda, and `adw_interface_search_open.py` for a destination it cannot pick.

## How the route is taken

`adapters/figure_route.FigureQuestionRoute` is Toyota's `IRouteOverride`: a
question asking for a real figure reaches here whether or not the model emits
the ROUTE line, the same way `StatusQuestionRoute` forces Mickey for Mazda.
It can afford to be generous, because an over-eager match ends at the ADW's
honest "that isn't a question I can ask the books yet" rather than at an
invented amount.

It vetoes requests that are about real money but belong to a more capable
agent: a spreadsheet is `category_spreadsheet`, a specific tax record is a
WORK command, report freshness is `progress_check`.
