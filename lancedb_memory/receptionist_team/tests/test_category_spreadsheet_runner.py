"""AdwCategorySpreadsheetRunner and the three interfaces it composes.

Nothing here touches MySQL, spawns a subprocess or calls a model: the reader
is a regex, the flag builder is a spelling, and the workflow is faked. That
is the point of the split -- the one judgement in the path (which category
was meant) is testable on its own.

    cd lancedb_memory
    .venv/bin/python -m receptionist_team.tests.test_category_spreadsheet_runner
"""
from ..adapters.category_spreadsheet_runner import (
    AdwCategorySpreadsheetRunner,
    CategorySpreadsheetFlags,
    RegexSpreadsheetAskReader,
)
from ..adapters.route_panel import FirstRouteOpinion
from ..adapters.spreadsheet_route import SpreadsheetRequestRoute
from ..roles import CATEGORY_SPREADSHEET_AGENT_NAME, LIVE_BOOKS_AGENT_NAME
from ..spreadsheets import SpreadsheetAsk


class _FakeWorkflow:
    """Records the command line it was handed; writes nothing, spawns nothing."""

    def __init__(self, line: str = 'ROL: wrote 15 expenses totalling $2,630.94 to /tmp/x.xlsx') -> None:
        self.seen: list[str] = []
        self.purpose = ''
        self._line = line

    def line(self, purpose: str, flags) -> str:
        self.purpose = purpose
        self.seen = list(flags)
        return self._line


# -- the reader: the only judgement in the path ------------------------------

async def test_it_strips_the_wrapper_and_keeps_the_category_name():
    read = RegexSpreadsheetAskReader().read
    assert read('spreadsheet for Church Electric Bill').category == 'Church Electric Bill'
    assert read('I need a workbook of Benefits and Medical').category == 'Benefits and Medical'
    assert read('could you build a spreadsheet of Gardner Clinic').category == 'Gardner Clinic'


async def test_office_keeps_its_first_two_letters():
    # "Office" begins with a bare "of"; a connector pattern without word
    # boundaries turned the category into "fice" and matched no row.
    ask = RegexSpreadsheetAskReader().read('Office spreadsheet with sub-categories as a csv')
    assert ask.category == 'Office'
    assert ask.include_children and ask.out_format == 'csv'


async def test_it_hears_a_year_a_comparison_and_an_id():
    read = RegexSpreadsheetAskReader().read
    ask = read('make me a spreadsheet of Medical expenses for 2025 compared to last year')
    assert (ask.category, ask.year, ask.compare) == ('Medical', 2025, True)
    assert read('csv of category id 345').category_id == 345


async def test_two_years_is_not_one_year():
    # "compare 2025 with 2026" is two sheets; taking the first silently
    # would answer a question nobody asked.
    assert RegexSpreadsheetAskReader().read('Medical for 2025 and 2026').year is None


async def test_no_category_named_is_none_rather_than_a_run():
    assert RegexSpreadsheetAskReader().read('build a spreadsheet') is None
    assert RegexSpreadsheetAskReader().read('') is None


# -- the ask: what it refuses to carry ---------------------------------------

async def test_compare_without_a_year_is_dropped_before_it_reaches_the_adw():
    # The ADW refuses the pair and writes nothing; dropping it here means
    # the user gets a sheet instead of a refusal for a flag they never typed.
    assert RegexSpreadsheetAskReader().read('medical spending versus last year').compare is False
    assert SpreadsheetAsk(category='Medical', compare=True).wanted().compare is False
    assert SpreadsheetAsk(category='Medical', year=2025, compare=True).wanted().compare is True


# -- the flag builder: the CLI's spelling ------------------------------------

async def test_the_id_is_passed_instead_of_the_name_never_beside_it():
    flags = CategorySpreadsheetFlags().flags(
        SpreadsheetAsk(category='Other', category_id=345, year=2025, out_format='csv'))
    assert '--category-id' in flags and '345' in flags
    assert '--category' not in flags
    assert flags[flags.index('--format') + 1] == 'csv'


async def test_compare_is_only_spelled_with_a_year_beside_it():
    spell = CategorySpreadsheetFlags().flags
    assert '--compare' in spell(SpreadsheetAsk(category='Medical', year=2025, compare=True))
    assert '--compare' not in spell(SpreadsheetAsk(category='Medical', compare=True))


# -- the runner: composed, with nothing real underneath ----------------------

async def test_it_repeats_the_adws_line_word_for_word():
    workflow = _FakeWorkflow()
    runner = AdwCategorySpreadsheetRunner(workflow=workflow)
    reply = await runner.run(object(), 'category_spreadsheet', 'fake-model',
                             extra='spreadsheet of Church Electric Bill')
    assert reply.text == 'ROL: wrote 15 expenses totalling $2,630.94 to /tmp/x.xlsx'
    assert workflow.seen[workflow.seen.index('--category') + 1] == 'Church Electric Bill'
    assert workflow.seen[workflow.seen.index('--org-type') + 1] == 'NONPROFIT'


async def test_it_asks_which_category_rather_than_running_without_one():
    workflow = _FakeWorkflow()
    runner = AdwCategorySpreadsheetRunner(workflow=workflow)
    reply = await runner.run(object(), 'category_spreadsheet', 'fake-model',
                             extra='make me a spreadsheet')
    assert 'which category' in reply.text.lower()
    assert workflow.seen == []


# -- the route: who gets the turn --------------------------------------------

def _asked(text: str) -> str:
    """A prompt in the shape adapters/latest_user.py reads."""
    return f'USER: {text}\nAGENT: '


async def test_a_spreadsheet_request_beats_a_figure_question():
    # Word for word, "what did we spend on utilities" is also a figure
    # question. The narrower route goes first on purpose.
    panel = FirstRouteOpinion([
        SpreadsheetRequestRoute(CATEGORY_SPREADSHEET_AGENT_NAME),
        _AlwaysFigure(),
    ])
    assert panel.forced_agent(_asked(
        'what did we spend on utilities, as a spreadsheet')) == CATEGORY_SPREADSHEET_AGENT_NAME


async def test_a_plain_figure_question_is_left_to_the_next_route():
    panel = FirstRouteOpinion([
        SpreadsheetRequestRoute(CATEGORY_SPREADSHEET_AGENT_NAME),
        _AlwaysFigure(),
    ])
    assert panel.forced_agent(_asked('what did we spend on utilities')) == LIVE_BOOKS_AGENT_NAME


async def test_an_empty_panel_is_refused_rather_than_silently_agreeing():
    try:
        FirstRouteOpinion([])
    except ValueError:
        return
    raise AssertionError('an empty FirstRouteOpinion should raise')


class _AlwaysFigure:
    """A stand-in for FigureQuestionRoute: claims every turn."""

    def forced_agent(self, prompt: str) -> str | None:
        return LIVE_BOOKS_AGENT_NAME

    def question(self, prompt: str) -> str:
        return prompt


if __name__ == '__main__':
    from tests.runner import main
    main(dict(globals()))
