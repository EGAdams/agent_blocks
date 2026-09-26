"""Starter specialist briefs for Mazda's team; no SDK dependency.

MAZDA_TEAM_REPO_ROOT can point at a real input/output repository later. Until
then, the workspace next to this module is a self-contained placeholder.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = os.environ.get( "MAZDA_TEAM_REPO_ROOT", str( Path( __file__ ).resolve().parent / "workspace" ) )


@dataclass( frozen=True )
class AgentRole:
    name: str
    output_path: str
    system_prompt: str
    task_prompt: str
    allowed_tools: list[ str ] = field(
        default_factory=lambda: [ "Bash", "Read", "Write", "Glob", "Grep" ]
    )
    max_turns: int = 40


ROLES: dict[ str, AgentRole ] = {
    "sheeta": AgentRole(
        name="sheeta",
        output_path=f"{REPO_ROOT}/spreadsheets/sample_spreadsheet.csv",
        system_prompt=(
            "You are Sheeta, the lead responsible for a team creating spreadsheets. "
            "You do the work yourself today; more teammates may be added later. "
            "Use only local files and Python standard-library CSV; do not access "
            "a live database or rol_finances. This is a sample, not real business data."
        ),
        task_prompt=f"""\
Create `{REPO_ROOT}/spreadsheets/` if it does not exist. Write a clearly labeled
sample CSV at `{REPO_ROOT}/spreadsheets/sample_spreadsheet.csv` using the Python
standard library csv module. Include columns item,quantity,unit_price,total
and a small sample table with internally consistent totals. Label sample rows
as examples; do not treat these as real records. No live DB or rol_finances
access. Reply with one sentence naming the file and number of data rows.""",
    ),
    "tarita": AgentRole(
        name="tarita",
        output_path=f"{REPO_ROOT}/tax_forms/sample_form_1040.json",
        system_prompt=(
            "You are TaRita. Extract tax data from supplied source files and map "
            "it into IRS Form 1040 fields. No live intake source is wired up yet. "
            f"Check {REPO_ROOT}/tax_intake/ for input; create it if missing. "
            "If empty, produce a clearly labeled template, never invented tax "
            "figures. Do not use a live database or access rol_finances."
        ),
        task_prompt=f"""\
Create `{REPO_ROOT}/tax_intake/` and `{REPO_ROOT}/tax_forms/` if missing. Check
`{REPO_ROOT}/tax_intake/` for files. If present, extract only legible tax data
and map it into Form 1040-shaped JSON at
`{REPO_ROOT}/tax_forms/sample_form_1040.json`, using plain-language IRS field
names such as filing_status, wages_line_1a and total_income_line_9. Use your
judgment for exact names since no real form schema is wired up. Leave any
unknown field null; do not infer missing values. If there are no input files,
write a clearly labeled template JSON with those same fields set to null,
and say no intake data was found. This output is a starter mapping, not an
official filed form. No live DB or rol_finances access. Reply in one sentence
stating whether intake was found and how many fields were populated versus
templated.""",
    ),
}
