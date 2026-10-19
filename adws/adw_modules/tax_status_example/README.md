# Tax Status: the second playground ADW

```bash
cd /home/adamsl/agent_blocks/adws
uv run adw_debug_console.py
```

Choose **11. Run Tax Status ADW (5 code phases)**. No input is needed. It reads
the current ROL finance records, plan, and saved review history, produces a
report, and returns to the menu. **12** exits.

One selection starts one fresh **run**, identified by its printed `adw_id`.
The **ADW** defines these five **phases**, which run in order:

| Phase | Work |
| --- | --- |
| `read_tracked_questions` | Read the current plan and saved question history. |
| `count_category_work` | Read the database and count pending category decisions. |
| `check_question_progress` | Find unanswered questions, waiting items, and follow-up work. |
| `check_missing_amounts` | Check the work plan for amounts still needing a source. |
| `publish_progress_report` | Save HTML, Markdown, and expense-detail JSON reports. |

All five phases use ordinary code. There are no model calls, saved answers,
or changes to expense categories. Source unavailability makes the run fail
and labels its report incomplete, even if every phase executed successfully.
Successful execution does not mean the tax preparation itself is finished.

The implementation stays at `rol_finances/adws/adw_tax_status.py`, with its
finance modules, documents, and connection settings. The playground menu
launches it in a subprocess and supplies a configuration that saves the trace
and reports under **agent_blocks/adws/adw_data**. This is a connected learning
example: it requires the finance checkout and its current database connection.
Set `ROL_FINANCE_ROOT` to use a different finance checkout.

From `/home/adamsl/agent_blocks`, inspect the printed run:

```bash
just phases <adw_id>
just tail <adw_id>
```

The playground viewer serves the report over HTTP with its receipt and
statement links. From `/home/adamsl/agent_blocks`, start it with:

```bash
just obs
```

Open `http://localhost:4613`, select the tax-status run, and click **Open
remaining work report** in its event terminal. The report is a saved snapshot;
choose option 11 again for updated results and a new run ID. For the first run:
`http://localhost:4613/api/sessions/49228daa/reports/remaining-work.html`.
