"""Bridge specialists -- one per Bridge agent Toyota may drive beyond read-only.

    Toyota (Groq) -> ROUTE: bridge_writer | <the user's words>
      -> BridgeSpecialistRunner
         -> IFormFiller (one small Groq call): the user's words -> SpecialistForm (JSON)
         -> IRequestBuilder (code): SpecialistForm + the live GET /agents entry -> BridgeActionRequest
         -> read back + wait for "yes" when the operation changes a file or moves paper
         -> BridgeTaskWorkflow (submit, monitor, retrieve, respond) -> Toyota

The model only fills a form. Which Bridge agent a specialist sends to is fixed
here, never chosen by a model; the operation and workspace it fills in are
checked against the Bridge's own agent list, and the exact prompt a printer or
scanner accepts is built by code, never typed by the model.
"""
import json
import re
import uuid
from dataclasses import dataclass
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, ValidationError

from .browser_plans import BROWSER_PLANS
from .files import FILE_ID, TASK_ID, list_sentence, view_sentence
from .interfaces import IBridgeFiles
from .models import TASK_ID_PATTERN, BridgeActionRequest, BridgeAgent, OPERATION_MODES
from .notes_plans import NOTES_PLANS
from .deploy_plans import DEPLOY_PLANS
from .relay_plans import RELAY_PLANS

_TASK_ID = re.compile(TASK_ID_PATTERN)
WRITE_FRAME = ("Request relayed by Toyota, the receptionist; EG confirmed it out loud before it was sent. "
               "Do only what is asked, in this workspace. Finish with a short plain-sentence summary "
               "suitable to be read aloud.\n\n")
READ_FRAME = ("Read-only request relayed by Toyota, the receptionist. Do not modify any file. "
              "Answer briefly, in plain sentences suitable to be read aloud.\n\n")


class FormProblem(Exception):
    """The form can't become a request. `spoken` says why, or what to ask the user."""

    def __init__(self, spoken: str) -> None:
        super().__init__(spoken)
        self.spoken = spoken


class NoteEditForm(BaseModel):
    """One change for a Notes update: located by exact text, a line number, or a section heading."""

    model_config = ConfigDict(extra="ignore")

    find: str | None = None
    line: int | None = None
    section: str | None = None
    append: str | None = None
    replace: str = ""


class NoteMarkForm(BaseModel):
    """One highlight change: add / recolor / clear, by the exact text (or all)."""

    model_config = ConfigDict(extra="ignore")

    action: str = ""
    text: str | None = None
    color: str | None = None
    all: bool = False


class SpecialistForm(BaseModel):
    """What a specialist's model may fill in. Every field optional; extras ignored."""

    model_config = ConfigDict(extra="ignore")

    ask: str | None = None            # a question back to the user when the request is unclear
    operation: str | None = None
    workspace: str | None = None
    instructions: str | None = None   # code agents: what to do
    scanner: str | None = None        # scanner: window | freezer
    kind: str | None = None           # printer: text | test_page
    text: str | None = None
    title: str | None = None
    copies: int | None = None
    job_id: str | None = None         # printer: status or cancel of one job
    marker: str | None = None         # notes: a NOTESDIAG-<12 hex> marker to look for
    note_id: str | None = None        # notes: an approved note's filename, from a list
    expected_revision: str | None = None   # notes: filled by code from the last read, never by the model
    edits: list[NoteEditForm] | None = None   # notes: find / line / section changes
    marks: list[NoteMarkForm] | None = None   # notes: highlight changes
    query: str | None = None          # notes: words to search for
    category: int | None = None       # notes: 1-10 (0 = none, on rename)
    new_name: str | None = None       # notes: rename target
    trash_id: str | None = None       # notes: a trashed note, from trash_list
    known_revision: str | None = None  # notes: filled by code from the last read, never by the model
    url: str | None = None            # browser: where to open or navigate
    tab: str | None = None            # browser: a tab id, from browser tabs
    css: str | None = None            # browser: one control's selector, from form_controls
    label: str | None = None          # browser: what a control says on the screen
    option_text: str | None = None    # browser: what the dropdown item says
    value: str | None = None          # browser: a dropdown item's own token
    limit: int | None = None          # browser: how many controls to list (or downloaded files to name)
    file_id: str | None = None        # browser: a downloaded file, f_ + 24 hex, from the downloaded-files list
    page: int | None = None           # browser: which page of a downloaded file
    text_offset: int | None = None    # browser: where in a downloaded file's text to read from
    task_id: str | None = None        # browser: only the files one download task saved
    instance: str | None = None       # chatgpt relay: which session, from its session list
    message: str | None = None        # chatgpt relay: what to say
    settle: str | None = None         # chatgpt relay: how to decide the reply is finished
    hops: int | None = None           # chatgpt relay: how many relays this message has already crossed
    origin: str | None = None         # chatgpt relay: which session it came from, so it never goes back there
    no_wait: bool | None = None       # chatgpt relay: deliver and end, without waiting for a reply
    last: int | None = None           # chatgpt relay: how many turns of the thread to read
    project: str | None = None        # deploy: a project name, from the project list
    reason: str | None = None         # deploy: why a deploy or rollback goes out (kept in the release record)
    release_id: str | None = None     # deploy: which release to roll back to, from history
    unit: str | None = None           # deploy: one of the project's services
    lines: int | None = None          # deploy: how many log lines to read
    dry_run: bool | None = None       # deploy: say what would change, change nothing

    @classmethod
    def parse(cls, raw: object) -> "SpecialistForm":
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raise FormProblem("My Bridge specialist didn't fill in the request properly, "
                                  "so I didn't send anything.") from None
        try:
            return cls.model_validate(raw)
        except ValidationError:
            raise FormProblem("My Bridge specialist filled in the request wrongly, so I didn't send anything.") from None


class IFormFiller(Protocol):
    """One model call: (specialist's system prompt, request) -> a JSON object."""

    def fill(self, system_prompt: str, request: str) -> dict: ...


class IRequestBuilder(Protocol):
    """Strategy: one specialist's form -> the exact Bridge request, or FormProblem."""

    def build(self, form: SpecialistForm, agent: BridgeAgent) -> tuple[BridgeActionRequest, str]:
        """The request (without idempotency key) and the read-back sentence."""
        ...

    def context(self, agent: BridgeAgent) -> str:
        """What the model is told about the live agent, beside the user's words."""
        ...


@runtime_checkable
class IDirectReader(Protocol):
    """A specialist operation that is answered by reading, not by a Bridge task: no submit, no yes, no wait.
    `direct` returns the spoken answer, or None when the form is an ordinary task for `build`."""

    def direct(self, form: SpecialistForm, files: Callable[[], IBridgeFiles]) -> str | None:
        ...


def _words(text: str | None, limit: int = 140) -> str:
    text = " ".join((text or "").split()).rstrip(".")
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _request(agent: BridgeAgent, workspace: str, operation: str, prompt: str) -> BridgeActionRequest:
    try:
        return BridgeActionRequest(agent=agent.name, workspace=workspace, operation=operation,
                                   prompt=prompt[:8000], approval_mode=OPERATION_MODES[operation])
    except (ValidationError, KeyError):
        raise FormProblem(f"That doesn't fit what {agent.name} accepts on the Bridge, "
                          "so I didn't send anything.") from None


class CodeTaskBuilder:
    """codex-writer / claude-sonnet: implement in a write workspace, or read in any workspace."""

    def __init__(self, operations: tuple[str, ...], default_workspace: str) -> None:
        self._operations = operations
        self._default_workspace = default_workspace

    def _offered(self, agent: BridgeAgent) -> list[str]:
        return [op for op in self._operations if op in agent.operations]

    def context(self, agent: BridgeAgent) -> str:
        return (f"Operations: {', '.join(self._offered(agent))}\n"
                f"Workspaces it can write in: {', '.join(agent.write_workspaces) or 'none'}\n"
                f"All workspaces: {', '.join(agent.workspaces)}\n"
                f"Default workspace: {self._default_workspace}")

    def build(self, form: SpecialistForm, agent: BridgeAgent) -> tuple[BridgeActionRequest, str]:
        offered = self._offered(agent)
        operation = (form.operation or offered[0] if offered else "").strip().lower()
        if operation not in offered:
            raise FormProblem(f"{agent.name} doesn't do {operation or 'that'} on the Bridge, so I didn't send anything.")
        workspace = (form.workspace or self._default_workspace).strip().lower()
        if workspace not in agent.workspaces:
            raise FormProblem(f"{agent.name} doesn't work in {workspace}, so I didn't send anything.")
        if operation == "implement" and workspace not in agent.write_workspaces:
            raise FormProblem(f"{agent.name} may not write in {workspace}, so I didn't send anything.")
        instructions = (form.instructions or "").strip()
        if not instructions:
            raise FormProblem("What should the Bridge do? I didn't catch the instructions.")
        frame = WRITE_FRAME if operation == "implement" else READ_FRAME
        verb = "change" if operation == "implement" else "look at"
        readback = f"I'll ask {agent.name} to {verb} {workspace}: {_words(instructions)}."
        return _request(agent, workspace, operation, frame + instructions), readback


class PrinterBuilder:
    """printer: a text page or the test page, a job's status, a cancel, or the printer list."""

    OPERATIONS = ("print", "print_status", "print_cancel", "print_discover")
    MAX_TEXT = 6000

    def context(self, agent: BridgeAgent) -> str:
        return f"Operations: {', '.join(op for op in self.OPERATIONS if op in agent.operations)}"

    def build(self, form: SpecialistForm, agent: BridgeAgent) -> tuple[BridgeActionRequest, str]:
        operation = (form.operation or "").strip().lower()
        if operation not in self.OPERATIONS or operation not in agent.operations:
            raise FormProblem("I couldn't tell what to do with the printer. Print some text, print a test page, "
                              "check a print job, cancel one, or list the printers?")
        workspace = agent.workspaces[0] if agent.workspaces else "printers"
        if operation == "print_discover":
            return _request(agent, workspace, operation, "discover"), ""
        if operation in ("print_status", "print_cancel"):
            job = (form.job_id or "").strip()
            if operation == "print_status" and not job:
                return _request(agent, workspace, operation, "recent"), ""
            if not _TASK_ID.fullmatch(job):
                raise FormProblem("Which print job? Give me its id, the one that starts with t_.")
            if operation == "print_status":
                return _request(agent, workspace, operation, f"job={job}"), ""
            return _request(agent, workspace, operation, f"cancel={job}"), f"I'll cancel print job {job}."
        copies = form.copies or 1
        if not 1 <= copies <= 10:
            raise FormProblem("I can print 1 to 10 copies.")
        if (form.kind or "").strip().lower() == "test_page":
            req, said = {"kind": "test_page"}, "the printer's test page"
        else:
            text = (form.text or "").strip()
            if not text:
                raise FormProblem("What should I print? I didn't catch the text.")
            if len(text) > self.MAX_TEXT:
                raise FormProblem("That's too much text for one print; shorten it.")
            req, said = {"kind": "text", "text": text}, f'a page that says "{_words(text, 80)}"'
            if form.title:
                req["title"] = _words(form.title, 120)
        if copies != 1:
            req["options"] = {"copies": copies}
        prompt = json.dumps(req, separators=(",", ":"), ensure_ascii=False)
        count = "" if copies == 1 else f", {copies} copies,"
        return _request(agent, workspace, operation, prompt), f"I'll print {said}{count} on the default printer."


class ScannerBuilder:
    """scanner: start one scan on the Window or Freezer scanner. Scala files the page."""

    SCANNERS = ("window", "freezer")

    def context(self, agent: BridgeAgent) -> str:
        return "Scanners: window, freezer"

    def build(self, form: SpecialistForm, agent: BridgeAgent) -> tuple[BridgeActionRequest, str]:
        scanner = (form.scanner or "").strip().lower()
        if scanner not in self.SCANNERS:
            raise FormProblem("Which scanner, the Window one or the Freezer one?")
        workspace = agent.workspaces[0] if agent.workspaces else "scanners"
        return (_request(agent, workspace, "scan", f"scanner={scanner}"),
                f"I'll start a scan on the {scanner.capitalize()} scanner, and Scala will file the page.")


class NotesBuilder:
    """notes: the whole Notes surface -- search, read, edit, highlight, create, rename/move, trash, restore, purge.

    Each operation is a Plan in `notes_plans.NOTES_PLANS` (Strategy + Registry); this class only finds it. The
    server's approved list and protected list still decide which notes may change, whatever is asked here.
    Revisions are remembered by code from the last read (see desk.NoteRevisions), never written by the model, and
    every operation that changes anything waits for a yes.
    """

    def context(self, agent: BridgeAgent) -> str:
        return "Operations: " + ", ".join(NOTES_PLANS)

    def build(self, form: SpecialistForm, agent: BridgeAgent) -> tuple[BridgeActionRequest, str]:
        what = (form.operation or "").strip().lower()
        plan = NOTES_PLANS.get(what)
        if plan is None:
            raise FormProblem("What should I do with the notes: list or search them, read one, change one, "
                              "highlight, create, rename, trash or restore one?")

        def problem(spoken: str) -> None:
            raise FormProblem(spoken)

        req, said = plan.build(form, problem)
        workspace = agent.workspaces[0] if agent.workspaces else "notes"
        prompt = json.dumps(req, separators=(",", ":"), ensure_ascii=False)
        return _request(agent, workspace, plan.operation, prompt), said


class BrowserBuilder:
    """browser: Chrome on EG's desktop -- open, navigate, read, tabs, click, and the three form operations.

    Each operation is a Plan in `browser_plans.BROWSER_PLANS` (Strategy + Registry); this class only finds it.
    This is the same Bridge agent, the same worker adapter and the same Browser Control ADW that ChatGPT drives,
    so Toyota gains no operation of her own and no second browser mechanism exists. The ADW still re-checks every
    request, refuses a consequential click that carries no authorization, and refuses a password field outright.
    """

    #: Answered by reading the gateway's file tools (files.py), never sent as a task.
    FILE_OPERATIONS = ("downloaded_files", "view_file")

    def context(self, agent: BridgeAgent) -> str:
        return "Operations: " + ", ".join((*BROWSER_PLANS, *self.FILE_OPERATIONS))

    @staticmethod
    def _what(form: SpecialistForm) -> str:
        return (form.operation or "").strip().lower().removeprefix("browser.").removeprefix("browser_")

    def direct(self, form: SpecialistForm, files: Callable[[], IBridgeFiles]) -> str | None:
        """List or read the files Toyota's own downloads saved. A read needs no yes: it changes nothing, and the
        gateway shows her only her own files. With no file id, "read the file I just downloaded" is the newest."""
        what = self._what(form)
        if what not in self.FILE_OPERATIONS:
            return None
        task_id = str(form.task_id or "").strip()
        if task_id and not TASK_ID.fullmatch(task_id):
            raise FormProblem("That doesn't look like a Bridge task id, so I didn't look anything up.")
        reader = files()
        if what == "downloaded_files":
            return list_sentence(reader.downloaded_files(task_id, max(1, min(form.limit or 5, 10))))
        file_id = str(form.file_id or "").strip()
        if file_id and not FILE_ID.fullmatch(file_id):
            raise FormProblem("That isn't a downloaded file's id. Ask me which files I've downloaded and use one "
                              "of those.")
        if not file_id:
            newest = reader.downloaded_files(task_id, 1).files
            if not newest:
                return list_sentence(reader.downloaded_files(task_id, 1))
            file_id = newest[0].file_id
        if form.page is not None and form.page < 1:
            raise FormProblem("Pages start at 1.")
        return view_sentence(reader.view_file(file_id, form.page, max(0, form.text_offset or 0)))

    def build(self, form: SpecialistForm, agent: BridgeAgent) -> tuple[BridgeActionRequest, str]:
        what = self._what(form)
        plan = BROWSER_PLANS.get(what)
        if plan is None:
            raise FormProblem("What should I do in the browser: open a page, go somewhere, read it, list its "
                              "form controls, choose in a dropdown, type in a field, or press something?")

        def problem(spoken: str) -> None:
            raise FormProblem(spoken)

        req, said = plan.build(form, problem)
        workspace = agent.workspaces[0] if agent.workspaces else "browser"
        prompt = json.dumps(req, separators=(",", ":"), ensure_ascii=False)
        return _request(agent, workspace, plan.operation, prompt), said


class ChatGptBuilder:
    """chatgpt_relay: one message into a standing, signed-in ChatGPT session on EG's machine, and its reply back.

    Each operation is a Plan in `relay_plans.RELAY_PLANS` (Strategy + Registry); this class only finds it. This
    is the same Bridge agent, the same worker adapter and the same relay ADW that ChatGPT drives, so Toyota
    gains no operation of her own and no second relay exists. Nothing here can close that browser -- it is
    signed in, and closing it costs the login -- and the relay still re-checks every request on its side,
    refuses a credential outright, and refuses a new chat that carries no authorization.
    """

    def context(self, agent: BridgeAgent) -> str:
        return "Operations: " + ", ".join(RELAY_PLANS)

    def build(self, form: SpecialistForm, agent: BridgeAgent) -> tuple[BridgeActionRequest, str]:
        what = (form.operation or "").strip().lower().removeprefix("relay.").removeprefix("chatgpt_")
        plan = RELAY_PLANS.get(what)
        if plan is None:
            raise FormProblem("What should I do with ChatGPT: ask it something, read the thread back, see which "
                              "sessions I can reach, or start its browser?")

        def problem(spoken: str) -> None:
            raise FormProblem(spoken)

        req, said = plan.build(form, problem)
        workspace = agent.workspaces[0] if agent.workspaces else "chatgpt-relay"
        prompt = json.dumps(req, separators=(",", ":"), ensure_ascii=False)
        return _request(agent, workspace, plan.operation, prompt), said


class DeployBuilder:
    """deploy: the Deployment ADW on EG's machine -- deploy, restart, roll back, check health, read logs.

    Each operation is a Plan in `deploy_plans.DEPLOY_PLANS` (Strategy + Registry); this class only finds it.
    The same Bridge agent, worker adapter and ADW (`adws/adw_deploy.py`) ChatGPT drives, so Toyota gains no
    deployment path of her own. Projects, commands and services come from the ADW's fixed project table; a
    request only names a project, and both the worker and the ADW re-check it.
    """

    def context(self, agent: BridgeAgent) -> str:
        return "Operations: " + ", ".join(DEPLOY_PLANS)

    def build(self, form: SpecialistForm, agent: BridgeAgent) -> tuple[BridgeActionRequest, str]:
        what = (form.operation or "").strip().lower().removeprefix("deploy.")
        plan = DEPLOY_PLANS.get(what)
        if plan is None:
            raise FormProblem("What should I do: list the projects, check one's status or health, read its logs "
                              "or history, deploy it, restart it, or roll it back?")

        def problem(spoken: str) -> None:
            raise FormProblem(spoken)

        req, said = plan.build(form, problem)
        workspace = agent.workspaces[0] if agent.workspaces else "deploy"
        prompt = json.dumps(req, separators=(",", ":"), ensure_ascii=False)
        return _request(agent, workspace, plan.operation, prompt), said


@dataclass(frozen=True)
class BridgeSpecialist:
    """One Toyota route that drives exactly one Bridge agent."""

    name: str           # Toyota's route name
    bridge_agent: str   # fixed: the only Bridge agent this specialist ever sends to
    spoken: str
    builder: IRequestBuilder


SPECIALISTS: dict[str, BridgeSpecialist] = {s.name: s for s in (
    BridgeSpecialist("bridge_writer", "codex-writer", "the Bridge's Codex writer",
                     CodeTaskBuilder(("implement",), "bridge_sandbox")),
    BridgeSpecialist("bridge_coder", "claude-sonnet", "the Bridge's Claude Sonnet coder",
                     CodeTaskBuilder(("implement", "inspect", "explain"), "bridge_sandbox")),
    BridgeSpecialist("bridge_printer", "printer", "the Bridge's printer", PrinterBuilder()),
    BridgeSpecialist("bridge_scanner", "scanner", "the Bridge's scanner", ScannerBuilder()),
    BridgeSpecialist("bridge_notes", "notes", "the Bridge's Notes", NotesBuilder()),
    BridgeSpecialist("bridge_browser", "browser", "the Bridge's browser", BrowserBuilder()),
    BridgeSpecialist("bridge_chatgpt", "chatgpt-relay", "the Bridge's ChatGPT relay", ChatGptBuilder()),
    BridgeSpecialist("bridge_deploy", "deploy", "the Bridge's Deployment ADW", DeployBuilder()),
)}
