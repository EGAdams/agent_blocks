"""State a receptionist's Bridge routes share between turns: the request waiting for a
"yes", and the tasks she promised to report on when they finish.

The read-back lives in this process only: restarting her drops an unanswered
one, and since nothing was sent, nothing is lost. The tasks she promised to
report on are also kept in a small JSON file (`NOTICES_FILE`), because a
restart between "I'll tell you when it finishes" and the finish used to drop
the promise silently -- on 2026-11-12 a deploy restarted her two minutes after
a scan finished, and the notice saying where it was filed vanished with it.

Each receptionist with Bridge access has her own `BridgeDesk` (`DESKS`, `desk_for`):
Toyota's is the module names below, kept for the code and tests that name them;
Sydney's has its own credential prefix, pending "yes", revisions and notices file.

Import-light on purpose: profiles.py builds the route overrides from a desk's
pending request at import time, and that must not open a socket or import a client.
"""
import json
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .models import BridgeActionRequest, TERMINAL_STATES

CONFIRM_SECONDS = 300.0
#: Where Toyota's promised notices survive a restart. Gitignored state, never committed.
NOTICES_FILE = Path(os.environ.get("TOYOTA_BRIDGE_NOTICES",
                                   "~/.local/state/receptionist_team/toyota_bridge_notices.json")).expanduser()

_YES = re.compile(r"^(?:yes|yeah|yep|yup|sure|ok(?:ay)?|go(?: ahead)?|do it|send it|please do|confirm(?:ed)?|"
                  r"affirmative|make it so|engage)\b", re.IGNORECASE)
_NO = re.compile(r"^(?:no|nope|nah|don'?t|do not|cancel|stop|never ?mind|forget it|hold off|abort)\b", re.IGNORECASE)


def yes_or_no(text: str) -> str | None:
    """'yes', 'no', or None. Only a short reply counts, so a new request that
    happens to start with "no" ("no, print the other one") isn't a bare no."""
    words = re.sub(r"[^\w\s']", " ", text or "").split()
    if not words or len(words) > 6:
        return None
    said = " ".join(words)
    if _NO.match(said):
        return "no"
    if _YES.match(said):
        return "yes"
    return None


@dataclass(frozen=True)
class PendingRequest:
    specialist: str
    request: BridgeActionRequest
    readback: str
    created: float


class PendingBridgeRequests:
    """At most one request waiting for a yes. A newer one replaces it."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, ttl: float = CONFIRM_SECONDS) -> None:
        self._clock, self._ttl = clock, ttl
        self._lock = threading.Lock()
        self._pending: PendingRequest | None = None

    def put(self, specialist: str, request: BridgeActionRequest, readback: str) -> None:
        with self._lock:
            self._pending = PendingRequest(specialist, request, readback, self._clock())

    def peek(self) -> PendingRequest | None:
        with self._lock:
            if self._pending and self._clock() - self._pending.created > self._ttl:
                self._pending = None
            return self._pending

    def take(self) -> PendingRequest | None:
        with self._lock:
            pending, self._pending = self._pending, None
        if pending and self._clock() - pending.created > self._ttl:
            return None
        return pending


class NoteRevisions:
    """The revision of each note Toyota last read or wrote, in this process only.

    An update must name the revision a read returned, and Toyota is not asked
    to say a 64-character hash out loud. Code remembers it instead; the
    worker still refuses the update if the note has moved since.
    """

    LIMIT = 50

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._known: dict[str, str] = {}

    def remember(self, note_id: str, revision: str) -> None:
        with self._lock:
            self._known.pop(note_id, None)
            self._known[note_id] = revision
            while len(self._known) > self.LIMIT:
                self._known.pop(next(iter(self._known)))

    def get(self, note_id: str) -> str | None:
        with self._lock:
            return self._known.get(note_id)


@dataclass
class _Watched:
    task_id: str
    label: str
    checked: float = 0.0
    spoken: str = ""
    on_finish: Callable[[object], None] | None = None


class BridgeCompletionNotices:
    """IAlertInbox: tasks Toyota sent that hadn't finished when she answered.

    Checked when asked (the dashboard polls her /alerts every 10 s, and each
    turn starts by reading them), at most once per task per `every` seconds,
    so there is no thread to keep alive. A finished task becomes one notice,
    spoken once and held until acknowledged. Watching stops after `give_up`
    seconds; the task can still be read back by id.

    With a `store` path, the watched tasks and their spoken notices are written there on every change and read
    back at start, so a restart neither forgets a promise nor loses a notice nobody has acknowledged yet. An
    `on_finish` Observer is not kept: it remembers a note revision, which a restart drops anyway.
    """

    PREFIX = "bridge-"

    def __init__(self, client_factory: Callable[[], object], speak: Callable[[object], str],
                 clock: Callable[[], float] = time.monotonic, every: float = 15.0, give_up: float = 3600.0,
                 background: bool = False, store: Path | None = None,
                 wall: Callable[[], float] = time.time, thread_name: str = "toyota-bridge-notices") -> None:
        self._client_factory = client_factory
        self._thread_name = thread_name
        self._speak = speak
        self._clock, self._every, self._give_up = clock, every, give_up
        self._lock = threading.Lock()
        self._watched: dict[str, _Watched] = {}
        self._started: dict[str, float] = {}
        self._background = background
        self._refreshing = False
        self._monitors: dict = {}
        self._states: dict = {}
        self._store, self._wall = store, wall
        self._load()

    # -- the file that outlives a restart ----------------------------------------
    def _load(self) -> None:
        if self._store is None:
            return
        try:
            rows = json.loads(self._store.read_text())
        except (OSError, ValueError):
            return  # no file yet, or unreadable: start empty rather than refuse to start
        now, wall = self._clock(), self._wall()
        for row in rows if isinstance(rows, list) else []:
            try:
                task_id, label = str(row["task_id"]), str(row["label"])
                age = max(0.0, wall - float(row["started_at"]))
            except (KeyError, TypeError, ValueError):
                continue
            self._watched[task_id] = _Watched(task_id, label, spoken=str(row.get("spoken") or ""))
            self._started[task_id] = now - age

    def _save(self) -> None:
        """Called with the lock held. A failed write costs only the restart guarantee, never the notice."""
        if self._store is None:
            return
        now, wall = self._clock(), self._wall()
        rows = [{"task_id": w.task_id, "label": w.label, "spoken": w.spoken,
                 "started_at": wall - (now - self._started.get(w.task_id, now))} for w in self._watched.values()]
        try:
            self._store.parent.mkdir(parents=True, exist_ok=True)
            part = self._store.with_suffix(".part")
            part.write_text(json.dumps(rows, indent=1))
            part.replace(self._store)
        except OSError:
            pass

    def track(self, task_id: str, label: str, on_finish: Callable[[object], None] | None = None) -> bool:
        """Watch `task_id`. `on_finish(result)` (an Observer) is told the finished result once, before it is
        spoken -- so whatever a caller learns from an answer it got in time, it also learns from a late one."""
        with self._lock:
            if len(self._watched) >= 100 and task_id not in self._watched:
                return False
            self._watched.setdefault(task_id, _Watched(task_id, label, on_finish=on_finish))
            self._started.setdefault(task_id, self._clock())
            self._save()
            return True

    def watching(self) -> list[str]:
        with self._lock:
            return [w.task_id for w in self._watched.values() if not w.spoken]

    def pending(self) -> list[dict]:
        if self._background:
            with self._lock:
                start = bool(self._watched) and not self._refreshing
                if start:
                    self._refreshing = True
            if start:
                threading.Thread(target=self._background_refresh, daemon=True, name=self._thread_name).start()
        else:
            self._refresh()
        with self._lock:
            return [{"id": self.PREFIX + w.task_id, "task_id": w.task_id, "kind": "bridge_finished",
                     "title": f"Star Trek Bridge: {w.label} finished", "spoken": w.spoken}
                    for w in self._watched.values() if w.spoken]

    def acknowledge(self, alert_ids: list[str] | None = None) -> int:
        with self._lock:
            done = [w for w in self._watched.values() if w.spoken
                    and (alert_ids is None or self.PREFIX + w.task_id in alert_ids)]
            for w in done:
                del self._watched[w.task_id]
                self._started.pop(w.task_id, None)
                self._monitors.pop(w.task_id, None)
                self._states.pop(w.task_id, None)
            if done:
                self._save()
            return len(done)

    def lifecycle(self, task_id: str):
        """Latest bounded status evidence; no extra HTTP request on the voice thread."""
        with self._lock:
            return self._states.get(task_id)

    def _background_refresh(self) -> None:
        try:
            self._refresh()
        finally:
            with self._lock:
                self._refreshing = False

    def _refresh(self) -> None:
        now = self._clock()
        with self._lock:
            due = [w for w in self._watched.values() if not w.spoken and now - w.checked >= self._every]
            for w in due:
                w.checked = now
            expired = [w for w in self._watched.values() if not w.spoken and now - self._started[w.task_id] > self._give_up]
            for w in expired:
                del self._watched[w.task_id]
                self._started.pop(w.task_id, None)
                self._monitors.pop(w.task_id, None)
                self._states.pop(w.task_id, None)
            if expired:
                self._save()
            due = [w for w in due if w.task_id in self._watched]
        if not due:
            return
        try:
            client = self._client_factory()
        except Exception:
            return
        for w in due:
            try:
                from .monitor import TaskMonitor
                view = client.status(w.task_id)
                with self._lock:
                    monitor = self._monitors.setdefault(w.task_id, TaskMonitor(w.task_id, client))
                    self._states[w.task_id] = monitor.observe(view)
                if view.state not in TERMINAL_STATES:
                    continue
                result = client.result(w.task_id)
                if w.on_finish is not None:
                    try:
                        w.on_finish(result)
                    except Exception:
                        pass  # an observer's failure never costs the spoken notice
                spoken = self._speak(result)
            except Exception:
                continue  # unreachable now; asked again in `every` seconds
            with self._lock:
                if w.task_id in self._watched:
                    self._watched[w.task_id].spoken = spoken
                    self._save()


class CombinedAlerts:
    """Composite IAlertInbox: the post office's failure alerts plus Bridge notices."""

    def __init__(self, *inboxes) -> None:
        self._inboxes = inboxes

    def pending(self) -> list[dict]:
        alerts: list[dict] = []
        for inbox in self._inboxes:
            alerts.extend(inbox.pending())
        return alerts

    def acknowledge(self, alert_ids: list[str] | None = None) -> int:
        total = 0
        for inbox in self._inboxes:
            if isinstance(inbox, BridgeCompletionNotices):
                ids = alert_ids
            else:  # never hand the post office an id it didn't issue
                ids = None if alert_ids is None else [i for i in alert_ids if not i.startswith(BridgeCompletionNotices.PREFIX)]
                if ids == [] and alert_ids:
                    continue
            total += inbox.acknowledge(ids)
        return total


def _client_for(prefix: str, kind: str, user_agent: str):
    from .client import BridgeClient
    from .config import load_client_config, load_client_token
    from .tracing import TracingBridgeClient
    from .transport import HttpsBridgeTransport
    transport = HttpsBridgeTransport(load_client_config(prefix=prefix), load_client_token(prefix=prefix),
                                     user_agent=user_agent)
    return TracingBridgeClient(BridgeClient(transport), kind=kind)


def _default_client():
    # Each submission is recorded as a toyota.bridge_submit object (bridge_client/tracing.py).
    return _client_for("TOYOTA", "toyota.bridge_submit", "toyota-bridge-client/1.0")


def _speak(result) -> str:
    from .workflow import RespondPhase
    return RespondPhase.result_sentence(result)


@dataclass(frozen=True)
class BridgeDesk:
    """Everything one receptionist's Bridge routes share: her credential (through `client_factory` and
    `files_factory`), the request waiting for her "yes", the note revisions she last read, and the tasks she
    promised to report on. Two receptionists never share one: Sydney's "yes" can't send Toyota's request,
    and each credential is bound on the gateway to its own sender, so the audit says who sent what.

    Role config, not a wire contract -- a frozen dataclass, same reasoning as ReceptionistProfile."""
    name: str
    pending: PendingBridgeRequests
    note_revisions: NoteRevisions
    note_active_items: NoteRevisions
    notices: BridgeCompletionNotices
    client_factory: Callable[[], object]
    files_factory: Callable[[], object]
    key_prefix: str

    def new_key(self) -> str:
        import uuid
        return f"{self.key_prefix}-{uuid.uuid4().hex}"


def _files_for(prefix: str) -> Callable[[], object]:
    def build():
        from .files import default_files
        return default_files(prefix)
    return build


def _sydney_client():
    return _client_for("SYDNEY", "sydney.bridge_submit", "sydney-bridge-client/1.0")


# Toyota's one desk: shared by her route override, her Bridge runners and her alert inbox.
PENDING = PendingBridgeRequests()
NOTE_REVISIONS = NoteRevisions()
NOTE_ACTIVE_ITEMS = NoteRevisions()   # the review item each note last answered as blinking, same rules
NOTICES = BridgeCompletionNotices(_default_client, _speak, background=True, store=NOTICES_FILE)

SYDNEY_NOTICES_FILE = Path(os.environ.get("SYDNEY_BRIDGE_NOTICES",
                                          "~/.local/state/receptionist_team/sydney_bridge_notices.json")).expanduser()

#: Registry of desks, by receptionist. A third receptionist with Bridge access is one entry here, one
#: `<NAME>_BRIDGE_CLIENT_TOKEN_FILE`, and one gateway binding for her own sender -- never a shared token.
DESKS: dict[str, BridgeDesk] = {
    "toyota": BridgeDesk("toyota", PENDING, NOTE_REVISIONS, NOTE_ACTIVE_ITEMS, NOTICES,
                         _default_client, _files_for("TOYOTA"), "toyota"),
    "sydney": BridgeDesk("sydney", PendingBridgeRequests(), NoteRevisions(), NoteRevisions(),
                         BridgeCompletionNotices(_sydney_client, _speak, background=True, store=SYDNEY_NOTICES_FILE,
                                                 thread_name="sydney-bridge-notices"),
                         _sydney_client, _files_for("SYDNEY"), "sydney"),
}


def desk_for(name: str) -> BridgeDesk:
    return DESKS[name]
