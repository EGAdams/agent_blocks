"""State Toyota's Bridge routes share between turns: the request waiting for a
"yes", and the tasks she promised to report on when they finish.

Both live in this process only (her sidecar or REPL). Restarting her drops an
unanswered read-back -- nothing was sent, so nothing is lost -- and the list of
tasks to report on; those can still be read back with "status t_...".

Import-light on purpose: profiles.py builds Toyota's route override from
PENDING at import time, and that must not open a socket or import a client.
"""
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from .models import BridgeActionRequest, TERMINAL_STATES

CONFIRM_SECONDS = 300.0

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


@dataclass
class _Watched:
    task_id: str
    label: str
    checked: float = 0.0
    spoken: str = ""


class BridgeCompletionNotices:
    """IAlertInbox: tasks Toyota sent that hadn't finished when she answered.

    Checked when asked (the dashboard polls her /alerts every 10 s, and each
    turn starts by reading them), at most once per task per `every` seconds,
    so there is no thread to keep alive. A finished task becomes one notice,
    spoken once and held until acknowledged. Watching stops after `give_up`
    seconds; the task can still be read back by id.
    """

    PREFIX = "bridge-"

    def __init__(self, client_factory: Callable[[], object], speak: Callable[[object], str],
                 clock: Callable[[], float] = time.monotonic, every: float = 15.0, give_up: float = 3600.0) -> None:
        self._client_factory = client_factory
        self._speak = speak
        self._clock, self._every, self._give_up = clock, every, give_up
        self._lock = threading.Lock()
        self._watched: dict[str, _Watched] = {}
        self._started: dict[str, float] = {}

    def track(self, task_id: str, label: str) -> None:
        with self._lock:
            self._watched.setdefault(task_id, _Watched(task_id, label))
            self._started.setdefault(task_id, self._clock())

    def watching(self) -> list[str]:
        with self._lock:
            return [w.task_id for w in self._watched.values() if not w.spoken]

    def pending(self) -> list[dict]:
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
            return len(done)

    def _refresh(self) -> None:
        now = self._clock()
        with self._lock:
            due = [w for w in self._watched.values() if not w.spoken and now - w.checked >= self._every]
            for w in due:
                w.checked = now
            for w in [w for w in self._watched.values() if not w.spoken and now - self._started[w.task_id] > self._give_up]:
                del self._watched[w.task_id]
                self._started.pop(w.task_id, None)
        if not due:
            return
        try:
            client = self._client_factory()
        except Exception:
            return
        for w in due:
            try:
                view = client.status(w.task_id)
                if view.state not in TERMINAL_STATES:
                    continue
                spoken = self._speak(client.result(w.task_id))
            except Exception:
                continue  # unreachable now; asked again in `every` seconds
            with self._lock:
                if w.task_id in self._watched:
                    self._watched[w.task_id].spoken = spoken


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


def _default_client():
    from .client import BridgeClient
    from .config import load_client_config, load_client_token
    from .transport import HttpsBridgeTransport
    return BridgeClient(HttpsBridgeTransport(load_client_config(), load_client_token()))


def _speak(result) -> str:
    from .workflow import RespondPhase
    return RespondPhase.result_sentence(result)


# Toyota's one desk: shared by her route override, her Bridge runners and her alert inbox.
PENDING = PendingBridgeRequests()
NOTICES = BridgeCompletionNotices(_default_client, _speak)
