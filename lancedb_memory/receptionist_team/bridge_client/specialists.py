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
from typing import Protocol

from pydantic import BaseModel, ConfigDict, ValidationError

from .models import TASK_ID_PATTERN, BridgeActionRequest, BridgeAgent, OPERATION_MODES

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
    """One find -> replace pair for a Notes update."""

    model_config = ConfigDict(extra="ignore")

    find: str
    replace: str = ""


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
    edits: list[NoteEditForm] | None = None   # notes: exact find -> replace pairs

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
    """notes: the Notes diagnostic, and reading and updating approved notes.

    The diagnostic ops touch only the Bridge's own diagnostic note. `list`,
    `read` and `update` go to approved notes by filename; the server's
    approved list and protected list decide which, whatever is asked here.
    The marker is made here, never by the model. An update's revision is the
    one code remembered from the last read (see desk.NoteRevisions), never
    something the model wrote, and an update always waits for a yes.
    """

    OPERATIONS = {"status": "inspect", "marker_write": "implement", "marker_clear": "implement",
                  "list": "inspect", "read": "inspect", "update": "implement"}
    MARKER = re.compile(r"NOTESDIAG-[0-9a-f]{12}")
    NOTE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,200}\.md")
    REVISION = re.compile(r"[0-9a-f]{64}")
    MAX_EDITS = 50

    def context(self, agent: BridgeAgent) -> str:
        return ("Operations: status, marker_write, marker_clear (the diagnostic note), "
                "list, read, update (approved notes, by note_id)")

    def build(self, form: SpecialistForm, agent: BridgeAgent) -> tuple[BridgeActionRequest, str]:
        what = (form.operation or "").strip().lower()
        if what not in self.OPERATIONS:
            raise FormProblem("Should I write a new Notes diagnostic marker, check the last one, clear it, "
                              "list the notes, read one, or update one?")
        workspace = agent.workspaces[0] if agent.workspaces else "notes"
        if what in ("list", "read", "update"):
            req, said = self._approved(what, form)
        else:
            req, said = self._diagnostic(what, form)
        prompt = json.dumps(req, separators=(",", ":"), ensure_ascii=False)
        return _request(agent, workspace, self.OPERATIONS[what], prompt), said

    def _diagnostic(self, what: str, form: SpecialistForm) -> tuple[dict, str]:
        found = self.MARKER.search(" ".join(filter(None, (form.marker, form.text, form.instructions))))
        req: dict = {"notes": what}
        if what == "marker_write":
            req["marker"] = f"NOTESDIAG-{uuid.uuid4().hex[:12]}"
            said = (f"I'll write the Notes diagnostic marker {req['marker']} to the diagnostic note only, "
                    "then read it back from the store and from the page's own endpoint.")
        elif what == "marker_clear":
            said = "I'll remove the Notes diagnostic note. No other note is touched."
        else:
            if found:
                req["marker"] = found.group(0)
            said = ""
        return req, said

    def _approved(self, what: str, form: SpecialistForm) -> tuple[dict, str]:
        if what == "list":
            return {"notes": "list"}, ""
        note_id = (form.note_id or "").strip()
        if not self.NOTE_ID.fullmatch(note_id):
            raise FormProblem("Which note? Ask me to list the notes, then give me the file name.")
        if what == "read":
            return {"notes": "read", "id": note_id}, ""
        revision = (form.expected_revision or "").strip().lower()
        if not self.REVISION.fullmatch(revision):
            raise FormProblem(f"I haven't read {note_id} yet in this conversation, so I can't change it safely. "
                              "Ask me to read it first.")
        edits = form.edits or []
        if not edits or len(edits) > self.MAX_EDITS or any(not e.find for e in edits):
            raise FormProblem("What exact text should change, and to what? I need the words as they appear now.")
        req = {"notes": "update", "id": note_id, "expected_revision": revision,
               "edits": [{"find": e.find, "replace": e.replace} for e in edits]}
        steps = "; ".join(f'"{_words(e.find, 60)}" to "{_words(e.replace, 60)}"' if e.replace
                          else f'remove "{_words(e.find, 60)}"' for e in edits[:5])
        more = f", and {len(edits) - 5} more" if len(edits) > 5 else ""
        said = (f"I'll change {note_id}, as I read it at revision {revision[:12]}: {steps}{more}. "
                "Each piece of text must match exactly once, or nothing is written.")
        return req, said


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
)}
