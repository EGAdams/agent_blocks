"""AdwCategorySpreadsheetRunner -- an IDelegationRunner that builds the sheet.

Deterministic first, the same posture as MickeyRunner, AdwBrowserRunner and
AdwLiveFinanceRunner: the work is a subprocess, the reply is the one line that
subprocess wrote, and no model stands between the two.

It matters here for a reason the other three only half share. The ADW's line
names a row count, a total and a path -- three claims a model in the path
could each soften into something that is nearly true. "About three thousand
dollars across fifteen expenses, saved to your spreadsheets folder" is a
sentence nobody can check against the file. So the line is repeated as
written, and the user can open the workbook and add up the column themselves.

WHAT IS INJECTED, AND WHY EACH

This runner composes three interfaces rather than doing the work:

* `ISpreadsheetAskReader` turns the user's words into a `SpreadsheetAsk`. This
  is the only judgement in the path, and it is isolated so it can be read and
  tested without a database, a subprocess or a model.
* `IAdwFlagBuilder` spells that ask as the ADW's flags. Separate because the
  CLI's spelling changes for different reasons than the user's phrasing does.
* `IAdwWorkflow` runs it and returns the written line, never raising.

The split is what keeps the dangerous judgement small. Picking the category is
the one place this runner could be wrong in a way that produces a plausible,
checkable-looking, wrong spreadsheet -- so it is one class with no I/O in it.
"""
import asyncio
import re

from agent_memory.models import AgentReply

from ..adw import IAdwFlagBuilder, IAdwWorkflow
from ..spreadsheets import ISpreadsheetAskReader, SpreadsheetAsk
from .adw_process import SpokenAdwFailure, SubprocessAdwWorkflow

ADW_SCRIPT = "adw_interface_category_spreadsheet.py"

# One category's rows out of ~2,500 on a local MySQL is milliseconds; writing
# the workbook is well under a second. This is the "something is stuck"
# ceiling, not an expected duration. A receptionist must not hang on it.
TIMEOUT_SECONDS = 120.0

# Which organization's books. The ADW resolves this to an id by
# organizations.type rather than a pinned number, so this stays the readable
# name of a choice instead of a magic 1.
ORG_TYPE = "NONPROFIT"


class RegexSpreadsheetAskReader:
    """Reads a category, a year and a format out of the user's own words.

    Implements ISpreadsheetAskReader. Everything it strips is a wrapper around
    the category rather than part of it -- "make me a spreadsheet of", "for
    2025", "as a csv" -- because what is left has to match a row in the
    categories table. It deliberately does NOT try to correct or normalize the
    name it ends up with: the ADW matches it exactly, then loosely, then
    refuses and lists the candidates, and that refusal is a better outcome
    than a guess this class made silently.
    """

    # A four-digit year. Narrow on purpose: a dollar amount, a count and a
    # record id are all digits too.
    _YEAR = re.compile(r"\b(20[12]\d)\b")

    # An explicitly named category id -- what the user says back after the ADW
    # has refused an ambiguous name and listed the candidates.
    _CATEGORY_ID = re.compile(
        r"\b(?:category\s*)?id\s*#?\s*(\d{1,6})\b|\(\s*id\s*(\d{1,6})\s*\)",
        re.IGNORECASE,
    )

    _CSV = re.compile(r"\b(csv|comma[- ]separated|plain text file)\b", re.IGNORECASE)
    _XLSX = re.compile(r"\b(xlsx|excel|workbook|spreadsheet)\b", re.IGNORECASE)

    # "versus last year", "compared to 2024", "year over year". Narrow on
    # purpose: "compare the two spreadsheets" is not this.
    _COMPARE = re.compile(
        r"\b(compare[ds]?\s+(to|with|against)|comparison|versus|vs\.?|"
        r"year[- ]over[- ]year|against (the )?(prior|previous|last) year|"
        r"(prior|previous|last) year)\b", re.IGNORECASE,
    )

    _CHILDREN = re.compile(
        r"\b(sub-?categor\w*|child categor\w*|everything (under|beneath|below)"
        r"|and its children|including (its )?sub)", re.IGNORECASE,
    )

    # Wrappers people put around the category name. Stripped so the needle is
    # "Church Electric Bill" and not "a spreadsheet of all the church electric
    # bill expenses please", which matches no row in any table.
    _LEAD_IN = re.compile(
        r"^(please\s+)?((can|could|would)\s+you\s+)?"
        r"((i|we)\s*('d|\s+would)?\s*(need|want|like)\s+)?"
        r"((can|could|would)\s+you\s+)?"
        r"((make|build|create|generate|give|get|"
        r"send|export|produce|put together|pull)\s+)?(me\s+)?(a|an|the)?\s*"
        r"(new\s+)?(category\s+)?(spreadsheet|workbook|csv|xlsx|excel(\s+file)?|sheet|"
        # \b on both ends of the trailing connector: without it "Office"
        # starts with a bare "Of" and the category becomes "fice".
        r"file|export|list)?\s*(\b(of|for|on|with|covering|showing)\b)?\s*",
        re.IGNORECASE,
    )
    _NOISE = re.compile(
        r"\b(all (of )?(the|our)?|every|each|the|our|expenses?|spending|transactions?|"
        r"charges?|costs?|category|categories|please|for me|in the books|"
        r"from the books|real|live)\b",
        re.IGNORECASE,
    )
    _CONNECTORS = re.compile(
        r"\b(as|in|to)\s+(a|an)?\s*(csv|xlsx|excel|workbook|spreadsheet|file)\b|"
        r"\b(for|in|during|from)\s+(the\s+)?(year\s+)?$",
        re.IGNORECASE,
    )

    def read(self, text: str) -> SpreadsheetAsk | None:
        """The ask, or None when no category was named at all.

        None is the honest outcome for "make me a spreadsheet" with nothing
        after it. Running the ADW anyway would make it refuse for a reason
        ("name the category") the receptionist can give more kindly herself.
        """
        raw = (text or "").strip()
        if not raw:
            return None

        found_id = self._CATEGORY_ID.search(raw)
        category_id = int(next(g for g in found_id.groups() if g)) if found_id else None

        years = self._YEAR.findall(raw)
        # Exactly one year, or none: "compare 2025 with 2026" is two sheets,
        # and silently taking the first would answer a different question.
        year = int(years[0]) if len(set(years)) == 1 else None

        # csv wins over xlsx when both appear, because "spreadsheet" matches
        # the xlsx pattern and is how people say "any of these" -- a bare
        # "spreadsheet as a csv" means csv.
        out_format = "csv" if self._CSV.search(raw) else "xlsx"

        category = self._needle(raw, year, found_id)
        if not category and category_id is None:
            return None
        return SpreadsheetAsk(
            category=category,
            category_id=category_id,
            year=year,
            include_children=bool(self._CHILDREN.search(raw)),
            compare=bool(self._COMPARE.search(raw)),
            out_format=out_format,
        ).wanted()

    # Words that are only ever glue around the name. Trimmed off both ends
    # after the patterns above have run, because those leave their own
    # seams: "spreadsheet for Church Electric Bill" has no verb for _LEAD_IN
    # to match and keeps its "for", and "Office spreadsheet with
    # sub-categories as a csv" is left as "Office with as a". Either needle
    # matches no row, so the ADW would refuse a request it could have
    # answered. Only the ENDS are trimmed -- "Benefits and Medical" is a real
    # category name and its middle "and" has to survive.
    _GLUE = {"a", "an", "the", "of", "for", "on", "with", "in", "to", "as",
             "and", "covering", "showing", "about", "from", "please", "me"}

    def _trim(self, text: str) -> str:
        """Drop glue words from either end until a real word is on both."""
        words = text.split()
        while words and words[0].lower() in self._GLUE:
            words.pop(0)
        while words and words[-1].lower() in self._GLUE:
            words.pop()
        return " ".join(words)

    def _needle(self, raw: str, year: int | None, found_id: re.Match | None) -> str:
        """What is left once every wrapper is taken off: the category name."""
        wanted = raw
        if found_id:
            wanted = wanted.replace(found_id.group(0), " ")
        if year is not None:
            wanted = re.sub(rf"\b(for|in|during|from)?\s*{year}\b", " ", wanted)
        wanted = self._LEAD_IN.sub("", wanted.strip())
        wanted = self._CSV.sub(" ", wanted)
        wanted = self._XLSX.sub(" ", wanted)
        wanted = self._CHILDREN.sub(" ", wanted)
        wanted = self._COMPARE.sub(" ", wanted)
        wanted = self._NOISE.sub(" ", wanted)
        wanted = self._CONNECTORS.sub(" ", wanted)
        wanted = re.sub(r"\s+", " ", wanted).strip(" ,.;:!?\"'")
        return self._trim(wanted)


class CategorySpreadsheetFlags:
    """Spells a SpreadsheetAsk as this ADW's command line.

    Implements IAdwFlagBuilder. The only class that knows the flags are
    called --category, --include-children and --compare; renaming one is an
    edit here.
    --category-id is passed INSTEAD of --category when it is known, because
    the ADW treats the id as the unambiguous answer and ignores the name.
    """

    def __init__(self, org_type: str = ORG_TYPE) -> None:
        self._org_type = org_type

    def purpose(self, ask: object) -> str:
        assert isinstance(ask, SpreadsheetAsk)
        return f"Toyota is building a category spreadsheet for the user: {ask.named()}"

    def flags(self, ask: object) -> list[str]:
        assert isinstance(ask, SpreadsheetAsk)
        flags = ["--org-type", self._org_type, "--format", ask.out_format]
        if ask.category_id is not None:
            flags += ["--category-id", str(ask.category_id)]
        else:
            flags += ["--category", ask.category]
        if ask.year is not None:
            flags += ["--year", str(ask.year)]
        if ask.include_children:
            flags.append("--include-children")
        # Guarded by the Ask's own `wanted()`, which drops compare when no
        # year was heard -- the ADW refuses the pair and would write no
        # spreadsheet for a user who only said "versus last year".
        if ask.compare and ask.year is not None:
            flags.append("--compare")
        return flags


class AdwCategorySpreadsheetRunner:
    """Runs the Category Spreadsheet ADW and reports the line it wrote."""

    def __init__(
        self,
        reader: ISpreadsheetAskReader | None = None,
        flag_builder: IAdwFlagBuilder | None = None,
        workflow: IAdwWorkflow | None = None,
    ) -> None:
        # Defaults, not requirements: profiles.py says AdwCategorySpreadsheetRunner()
        # and gets the working arrangement, while a test says
        # AdwCategorySpreadsheetRunner(workflow=FakeWorkflow()) and spawns
        # nothing. Same posture as the other runners' `python=None`.
        self._reader = reader or RegexSpreadsheetAskReader()
        self._flags = flag_builder or CategorySpreadsheetFlags()
        self._workflow = workflow or SubprocessAdwWorkflow(
            script=ADW_SCRIPT,
            voice=SpokenAdwFailure("the spreadsheet workflow", "spreadsheet for you"),
            timeout_seconds=TIMEOUT_SECONDS,
        )

    async def run(
        self, role: object, agent_name: str, model: str, extra: str = ""
    ) -> AgentReply:
        ask = self._reader.read(extra)
        if ask is None:
            return AgentReply(
                text=(
                    "I didn't catch which category you want the spreadsheet "
                    "for. Name the category and I'll build it -- say the year "
                    "too if you only want one."
                )
            )
        return AgentReply(text=await asyncio.to_thread(self._build, ask))

    def _build(self, ask: SpreadsheetAsk) -> str:
        return self._workflow.line(self._flags.purpose(ask), self._flags.flags(ask))
