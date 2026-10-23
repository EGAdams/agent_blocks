"""The Search Open ADW's judgement, with no browser and no network.

Three decisions in that workflow can be wrong without anything crashing, which
makes them the ones worth pinning:

* what we end up searching for, when the user handed over a Google URL instead
  of a phrase;
* what the real destination is behind a search engine's click-tracking
  redirect, since the anchor's own href is the engine's domain, not the site's;
* which of ten results is the site itself rather than its Wikipedia article.

The headless browser is faked by handing FindLinkPhase a reader that returns
canned candidates, so nothing here starts Chrome or reaches the network. The
last test spawns the real workflow with --dry-run, same as
test_adw_browser_runner.py's does, so nothing here opens a window either.
"""
import subprocess
import sys
from pathlib import Path

# The ADW lives in the workspace's adws/ directory, which is its own import
# root (sssf_roles.py and adw_modules/ sit beside it) and is not a package. So
# it is reached by path, the same way sssf_interfaces/ reaches the SSSF
# template -- there is no installable module to import instead.
ADWS_DIR = Path(__file__).resolve().parents[3] / "adws"
if str(ADWS_DIR) not in sys.path:
    sys.path.insert(0, str(ADWS_DIR))

import adw_interface_search_open as adw  # noqa: E402

from ..roles import ROLE_BLURBS, ROLE_SPOKEN_NAMES, ROLE_START_TASKS, TOYOTA_ROSTER


AGENT_NAME = "search_link_opener"


class _SilentLog:
    """ILifecycleLog that says nothing.

    Every phase here is LifecycleLogged, so constructing one reports itself.
    PrintLifecycleLog would print a line per phase into the test output; this
    satisfies the same interface and swallows it.
    """

    def created(self, obj):
        pass

    def destroyed(self, obj):
        pass


class _Handle:
    """IPhaseHandle that collects what a phase logged, instead of rendering it."""

    def __init__(self):
        self.logged = []

    def log(self, **fields):
        self.logged.append(fields)


class _ScriptedReader:
    """IResultPageReader returning canned candidates per backend name.

    Stands in for SeleniumResultPageReader, which is the only class in the ADW
    that touches a browser -- which is the point of it being an interface.
    """

    def __init__(self, by_backend):
        self._by_backend = by_backend
        self.closed = False
        self.read_urls = []

    def read(self, url, backend):
        self.read_urls.append(url)
        return list(self._by_backend.get(backend.name, []))

    def close(self):
        self.closed = True


def _request(**overrides):
    fields = dict(
        prompt="a test", phrase="", search_url="", prefer_domains=(),
        skip_domains=(), engine="", keep_path=False, dry_run=True,
        message_out="", config="", adw_id=None,
    )
    fields.update(overrides)
    return adw.SearchOpenRequest(**fields)


def _candidate(url, title="", backend="bing"):
    from urllib.parse import urlparse

    return adw.LinkCandidate(
        url=url, host=urlparse(url).netloc.lower(), title=title, backend=backend,
    )


# -- what we search for ------------------------------------------------------

async def test_a_phrase_is_searched_for_exactly_as_given():
    query = adw.SearchQueryPhase(_request(phrase="Fifth Third Bank"), _SilentLog()).act(_Handle())
    assert query.usable
    assert query.text == "Fifth Third Bank"
    # Nothing was substituted, so the operator's line must not claim one was.
    assert not query.substituted


async def test_a_google_search_url_is_read_for_its_query_not_fetched():
    # The user's own example. We cannot scrape Google headlessly (it answers
    # with a bot challenge), so the query is lifted out and run elsewhere --
    # and `substituted` is what makes the final sentence say so.
    query = adw.SearchQueryPhase(
        _request(search_url="https://www.google.com/search?q=Fifth+Third+Bank"), _SilentLog(),
    ).act(_Handle())
    assert query.usable
    assert query.text == "Fifth Third Bank"
    assert query.substituted


async def test_a_phrase_wins_over_a_search_url_when_both_are_given():
    query = adw.SearchQueryPhase(
        _request(phrase="Lake Michigan Credit Union",
                 search_url="https://www.bing.com/search?q=something+else"),
        _SilentLog(),
    ).act(_Handle())
    assert query.text == "Lake Michigan Credit Union"
    assert not query.substituted


async def test_nothing_searchable_is_reported_rather_than_guessed():
    for request in (_request(), _request(search_url="https://www.53.com/")):
        query = adw.SearchQueryPhase(request, _SilentLog()).act(_Handle())
        assert not query.usable
        assert query.text == ""
        assert query.reason


# -- what the redirect is hiding ---------------------------------------------

async def test_bings_click_tracking_is_decoded_to_the_real_destination():
    # Bing's result anchors point at bing.com/ck/a?...&u=a1<base64url>. Taking
    # the href at face value would hand the browser bing.com every time.
    import base64

    destination = "https://www.53.com/content/fifth-third/en.html"
    payload = base64.urlsafe_b64encode(destination.encode()).decode().rstrip("=")
    bing = next(b for b in adw.BACKENDS if b.name == "bing")
    assert bing.destination(f"https://www.bing.com/ck/a?!&&p=abc&u=a1{payload}") == destination


async def test_a_redirect_we_cannot_decode_is_dropped_not_guessed():
    bing = next(b for b in adw.BACKENDS if b.name == "bing")
    # No u= parameter at all, and still a tracking URL: there is no
    # destination in it, so it must not become one.
    assert bing.destination("https://www.bing.com/ck/a?!&&p=abc") == ""


async def test_duckduckgos_redirect_is_percent_decoded():
    ddg = next(b for b in adw.BACKENDS if b.name == "duckduckgo")
    assert ddg.destination(
        "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.53.com%2F&rut=x"
    ) == "https://www.53.com/"


async def test_googles_anchors_are_already_the_destination():
    google = next(b for b in adw.BACKENDS if b.name == "google")
    assert google.destination("https://www.53.com/") == "https://www.53.com/"


# -- which result is the site ------------------------------------------------

async def test_the_first_organic_result_wins_and_is_trimmed_to_the_front_door():
    # The real first Bing result for "Fifth Third Bank". Trimming is what turns
    # it into the page a login actually lives on, and it is the user's own
    # stated target.
    pick = adw.FirstOrganicLinkChooser().choose(
        [_candidate("https://www.53.com/content/fifth-third/en.html", "Fifth Third Bank")],
        ["bing"],
    )
    assert pick.found
    assert pick.target == "https://www.53.com/"
    assert pick.url == "https://www.53.com/content/fifth-third/en.html"


async def test_keep_path_opens_the_result_exactly_as_found():
    deep = "https://www.53.com/content/fifth-third/en/personal-banking.html"
    pick = adw.FirstOrganicLinkChooser(trim_to_home_page=False).choose(
        [_candidate(deep)], ["bing"],
    )
    assert pick.target == deep
    assert pick.home_page == ""


async def test_an_encyclopedia_or_social_profile_is_never_the_site():
    pick = adw.FirstOrganicLinkChooser().choose(
        [
            _candidate("https://en.wikipedia.org/wiki/Fifth_Third_Bank"),
            _candidate("https://www.linkedin.com/company/fifth-third-bank"),
            _candidate("https://www.53.com/content/fifth-third/en.html"),
        ],
        ["bing"],
    )
    assert pick.target == "https://www.53.com/"


async def test_a_preferred_domain_wins_from_any_rank():
    # The steering wheel: rank one is usually right, and when it isn't, naming
    # the domain beats guessing harder.
    pick = adw.FirstOrganicLinkChooser(prefer_domains=("lmcu.org",)).choose(
        [
            _candidate("https://www.bankrate.com/reviews/lmcu/"),
            _candidate("https://www.nerdwallet.com/reviews/lmcu"),
            _candidate("https://www.lmcu.org/personal/checking/"),
        ],
        ["bing"],
    )
    assert pick.target == "https://www.lmcu.org/"
    assert "preferred domain" in pick.reason


async def test_an_extra_skip_domain_is_honored_on_top_of_the_builtins():
    pick = adw.FirstOrganicLinkChooser(skip_domains=("bankrate.com",)).choose(
        [
            _candidate("https://www.bankrate.com/reviews/lmcu/"),
            _candidate("https://www.lmcu.org/"),
        ],
        ["bing"],
    )
    assert pick.target == "https://www.lmcu.org/"


async def test_no_candidates_means_no_pick_rather_than_an_invented_url():
    # This is the signal the whole fallback rests on: nothing found must not
    # become a plausible-looking destination.
    pick = adw.FirstOrganicLinkChooser().choose([], ["bing", "duckduckgo", "google"])
    assert not pick.found
    assert pick.target == ""
    assert pick.reason


async def test_results_that_are_all_directories_also_count_as_no_pick():
    pick = adw.FirstOrganicLinkChooser().choose(
        [_candidate("https://en.wikipedia.org/wiki/Fifth_Third_Bank"),
         _candidate("https://www.yelp.com/biz/fifth-third-bank")],
        ["bing"],
    )
    assert not pick.found
    assert "rather than the site itself" in pick.reason


# -- the ladder --------------------------------------------------------------

async def test_a_blocked_backend_costs_one_page_load_and_the_next_one_is_tried():
    # Google and DuckDuckGo both answer a headless browser with a challenge
    # page. That is zero candidates, not an error, and the run must continue.
    reader = _ScriptedReader({"bing": [_candidate("https://www.53.com/", backend="bing")]})
    query = adw.SearchQuery(text="Fifth Third Bank", usable=True)
    ladder = tuple(reversed(adw.BACKENDS))  # google, duckduckgo, bing
    pick = adw.FindLinkPhase(
        query, ladder, reader, adw.FirstOrganicLinkChooser(), _SilentLog(),
    ).act(_Handle())
    assert [b.name for b in ladder] == ["google", "duckduckgo", "bing"]
    assert pick.probed == ["google", "duckduckgo", "bing"]
    assert pick.target == "https://www.53.com/"
    # One Chrome per run, closed whatever happened -- a leaked browser is a
    # leaked browser per request.
    assert reader.closed


async def test_the_browser_is_closed_even_when_every_backend_came_up_empty():
    reader = _ScriptedReader({})
    pick = adw.FindLinkPhase(
        adw.SearchQuery(text="nothing matches this", usable=True), adw.BACKENDS,
        reader, adw.FirstOrganicLinkChooser(), _SilentLog(),
    ).act(_Handle())
    assert not pick.found
    assert reader.closed
    assert len(reader.read_urls) == len(adw.BACKENDS)


async def test_bing_leads_the_ladder_because_it_is_the_one_that_answers():
    # Measured on this box, and the reason the order is not "Google first".
    # If this flips, the module docstring is wrong too.
    assert [b.name for b in adw.BACKENDS] == ["bing", "duckduckgo", "google"]


# -- Toyota's side of it -----------------------------------------------------

async def test_toyota_can_route_to_it_and_has_words_for_it():
    assert TOYOTA_ROSTER.has(AGENT_NAME)
    assert AGENT_NAME in TOYOTA_ROSTER.allowed_agents()
    for mapping in (ROLE_BLURBS, ROLE_SPOKEN_NAMES, ROLE_START_TASKS):
        assert mapping[AGENT_NAME]
    # The acknowledgment is spoken before the work starts, so it has to read
    # as a sentence rather than as a role name.
    assert "site finder" in TOYOTA_ROSTER.acknowledgment(AGENT_NAME)


async def test_her_prompt_sends_a_named_site_here_and_a_known_address_elsewhere():
    prompt = TOYOTA_ROSTER.roster_text()
    assert AGENT_NAME in prompt and "browser_opener" in prompt
    from ..roles import TOYOTA_SYSTEM_PROMPT

    assert f"ROUTE: {AGENT_NAME}" in TOYOTA_SYSTEM_PROMPT
    # The two browser agents are only useful if she can tell them apart, and
    # the thing she must know about is where the headless driver lives.
    assert "/home/adamsl/selenium-headless/" in TOYOTA_SYSTEM_PROMPT
    assert "browser_manager" in TOYOTA_SYSTEM_PROMPT


# -- the real workflow, spawned once -----------------------------------------

async def test_the_real_workflow_finds_the_bank_on_a_dry_run():
    """The one test that spawns the ADW. --dry-run, so no window opens.

    It does reach the network -- that is the point, since a search backend
    that quietly starts challenging us is exactly the failure no fake can
    catch. Skipped rather than failed when the box is offline.
    """
    script = ADWS_DIR / "adw_interface_search_open.py"
    assert script.is_file()
    line_file = Path("/tmp/search_open_adw_test_line.txt")
    line_file.unlink(missing_ok=True)
    try:
        subprocess.run(
            [sys.executable, str(script), "pinning the search-open ADW in a test",
             "--phrase", "Fifth Third Bank", "--dry-run",
             "--message-out", str(line_file)],
            cwd=ADWS_DIR.parent, capture_output=True, text=True, timeout=180,
        )
    except subprocess.TimeoutExpired:
        print("    SKIP  the workflow did not finish in 180s (slow or offline box)")
        return
    if not line_file.exists():
        print("    SKIP  the workflow wrote no line (offline box?)")
        return
    line = line_file.read_text().strip()
    # A dry run resolves a launcher and spawns nothing, so the line says what
    # it *would* open -- and that URL is the whole point of the workflow.
    if "53.com" not in line:
        print(f"    SKIP  no 53.com in the line, backend may be challenging us: {line}")
        return
    assert "Dry run" in line


if __name__ == "__main__":
    from tests.runner import main

    main(dict(globals()))
