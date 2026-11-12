"""One Plan per Scala storage operation: the model's filled form -> the exact JSON prompt, and the sentence read back.

Strategy + Registry, the same shape as `deploy_plans.py`. `ScalaStorageBuilder` (specialists.py) finds the Plan
for `form.operation` in `STORAGE_PLANS` and asks it to `build()`; adding an operation is one class here and one
line in `STORAGE_PLANS`. The Plans mirror the worker's table (`agent_bridge_worker/scala_storage_agent.py`:
READS, WRITES, FLAGS, NEEDS), which re-checks everything, and the ADW (`adws/adw_scala_storage.py`) and the
helper it ships to the destination check it all again -- approved folder, machine identity, no symlinks,
SHA-256 after the copy.

What code decides, never the model:

- the operation's lane (`inspect` or `implement`), so a store can never be sent as a read;
- a store is always a copy: there is no move and no delete, and asking for one is answered in words;
- a request names a scan id, a machine and a folder -- never a path outside the machine's cabinet, never `..`;
- every store is read back and waits for a yes, like every other Bridge change Toyota makes.
"""
import re
from typing import Protocol

# The worker's own patterns (scala_storage_agent.FLAGS), so a value refused there is refused here first, in words.
SCAN_ID = re.compile(r"scan_[0-9A-HJKMNP-TV-Z]{26}")
MACHINE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 '’_-]{0,40}")
FOLDER = re.compile(r"[A-Za-z0-9_/. -]{1,200}")
LATEST = ("last", "latest")


class IStoragePlan(Protocol):
    name: str
    operation: str      # the Bridge lane: "inspect" or "implement"

    def build(self, form, problem) -> tuple[dict, str]:
        """(prompt dict, read-back sentence). `problem(spoken)` raises the specialist's FormProblem."""
        ...


def _scan(form, problem, required: bool) -> dict:
    raw = str(form.scan_id or "").strip()
    if not raw:
        if required:
            problem("Which scan? Give me its scan id, or say the last one.")
        return {}
    if raw.lower() in LATEST:
        return {"scan_id": raw.lower()}
    if raw[:5].lower() == "scan_":
        raw = "scan_" + raw[5:].upper()
    if not SCAN_ID.fullmatch(raw):
        problem("That isn't a scan id. They look like scan_ and then 26 letters and numbers; or say the last one.")
    return {"scan_id": raw}


def _machine(form, problem) -> str:
    name = " ".join(str(form.machine or "").split())
    if not name:
        problem("Which machine: BATI, R46 (Mom's), or SQMC?")
    if not MACHINE.fullmatch(name):
        problem("That isn't one of the machines. They are BATI, R46 (Mom's), and SQMC.")
    return name


def _folder(form, problem) -> str:
    folder = str(form.folder or "").strip()
    if not folder:
        problem("Which folder? Ask me for that machine's folders if you're not sure.")
    if ".." in folder or "~" in folder or "\\" in folder or not FOLDER.fullmatch(folder):
        problem("That isn't one of the approved folders. Ask me for that machine's folders.")
    return folder


def _which(req: dict) -> str:
    scan = req.get("scan_id", "")
    return "the last scan" if scan in LATEST else f"scan {scan}"


class Targets:
    name, operation = "targets", "inspect"

    def build(self, form, problem):
        return {"storage": "targets"}, ""


class _MachineRead:
    """folders / health: one machine, nothing changed."""

    operation = "inspect"

    def __init__(self, name: str) -> None:
        self.name = name

    def build(self, form, problem):
        return {"storage": self.name, "machine": _machine(form, problem)}, ""


class Verify:
    name, operation = "verify", "inspect"

    def build(self, form, problem):
        return {"storage": "verify", **_scan(form, problem, True), "machine": _machine(form, problem),
                "folder": _folder(form, problem)}, ""


class Retrieve:
    """Copies a filed scan back to SQMC's retrieved/ folder, hash-checked. Nothing on the machine changes."""

    name, operation = "retrieve", "inspect"

    def build(self, form, problem):
        return {"storage": "retrieve", **_scan(form, problem, True), "machine": _machine(form, problem)}, ""


class Status:
    name, operation = "status", "inspect"

    def build(self, form, problem):
        return {"storage": "status", **_scan(form, problem, False)}, ""


class Store:
    """Copy one scan into an approved folder; the original stays on the scanner."""

    name, operation = "store", "implement"

    def build(self, form, problem):
        req = {"storage": "store", **_scan(form, problem, True), "machine": _machine(form, problem),
               "folder": _folder(form, problem)}
        return req, (f"I'll copy {_which(req)} to {req['machine']}, in its {req['folder']} folder. "
                     "It's a copy, so the original stays where it is, and the copy is checked against the "
                     "original's fingerprint afterwards.")


STORAGE_PLANS: dict[str, IStoragePlan] = {p.name: p for p in (
    Targets(), _MachineRead("folders"), _MachineRead("health"), Verify(), Retrieve(), Status(), Store(),
)}
#: Spoken words that are not operations and must never become one. Scala only copies.
NEVER = ("move", "delete", "remove", "erase", "rename", "overwrite")
