# unset ANTHROPIC_API_KEY
# unset ANTHROPIC_BASE_URL
"""A receptionist as a long-lived local service, so the dashboard can talk to her.

Same receptionist as `python -m receptionist_team.main --agent <name>` -- the
same build_receptionist() composition root, the same LanceDB memory, the same
RoutingModel delegation. This module adds no conversational behaviour of its
own; it is an HTTP mouth on the existing agent, so the browser gets the REPL's
experience instead of a second implementation of it.

    cd lancedb_memory
    .venv/bin/python -m receptionist_team.service                  # Toyota, :8767
    .venv/bin/python -m receptionist_team.service --agent mazda    # Mazda,  :8768

WHICH RECEPTIONIST, AND ON WHICH PORT
`--agent` names a profiles.py entry and the port defaults to that profile's
`service_port`, so the two sidecars can run side by side and the dashboard can
hold a warm agent for each. Adding a third receptionist adds no code here.

WHY A SERVICE AND NOT A SUBPROCESS PER TURN
The `sentence` embedding profile loads a sentence-transformers model (torch)
and opens a LanceDB table. That costs seconds, and a REPL pays it once at
startup -- so the dashboard should too. One warm ConversationAgent therefore
lives here for the process's lifetime, and a turn is just an HTTP request.

WHY IT BINDS LOOPBACK ONLY
A delegated finance turn runs Claude with permission_mode="bypassPermissions"
and real Bash/Read/Write against the live rol_finances MySQL DB (see
finance_team/adapters/tool_agent.py). That is Toyota's delegation path today
and Mazda's the moment one of her specialists is wired to anything real, so
the rule is the sidecar's, not one receptionist's.
The dashboard on :8765 is 0.0.0.0-bound
and Tailscale-served to a phone; this service must not be. It listens on
127.0.0.1 and the dashboard proxies to it over loopback.
**Do not change the bind address to 0.0.0.0.**

TURNS ARE SERIALIZED
One warm agent means one conversation. Turns take an asyncio lock, so a
second request waits rather than interleaving two turns into the same
session's memory (recall-before-persist only holds if turns don't overlap).
"""
import argparse
import json
from typing import Any
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pydantic import ValidationError

from agent_memory.config import Settings

from .main import _load_dotenv
from .profiles import PROFILES, TOYOTA, ReceptionistProfile, profile as named_profile
from .service_models import MAX_TURN_CHARS, MemoryComposeRequest, MemoryRecordRequest, TurnRequest
from .turn_runner import _DelegationRelay, _TurnRunner

# Kept for the callers that imported it before Mazda had a sidecar of her own;
# every port now comes from the selected profile. 8766 is thought_bridge.py on
# this box, 8765 is the dashboard itself.
DEFAULT_PORT = TOYOTA.service_port

class _ReceptionistHandler(BaseHTTPRequestHandler):
    """GET /health, POST /turn, POST /compact, POST /memory/compose,
    POST /memory/record. Nothing else, and no static files."""

    server_version = "Receptionist/1.1"
    protocol_version = "HTTP/1.0"

    def do_GET(self) -> None:
        if self._route() == "/health":
            self._json(200, self.server.runner.health())
            return
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        route = self._route()
        if route == "/compact":
            # The dashboard's "compact conversation" button: rotate the warm
            # agent onto a fresh session id. A plain JSON reply, not NDJSON --
            # this isn't a turn, so it never touches Groq or finance_team.
            self._json(200, self.server.runner.clear_context())
            return
        if route in ("/memory/compose", "/memory/record"):
            # Shared LanceDB memory for a guest whose turns run elsewhere --
            # the dashboard's Letta Mazda, served by Toyota's sidecar. Answers
            # {"ok": false} rather than 404 on a sidecar that hosts no guest,
            # so a caller learns there is no shared memory here, not that the
            # route is gone. Plain JSON in and out; no Groq, no finance_team.
            model = MemoryComposeRequest if route == "/memory/compose" else MemoryRecordRequest
            request = self._read(model, MAX_TURN_CHARS * 24)
            if request is None:
                return
            runner = self.server.runner
            try:
                if route == "/memory/compose":
                    self._json(200, runner.compose_memory(request))
                else:
                    self._json(200, runner.record_memory(request))
            except Exception as exc:
                print(f"[{self._log_name()}] {route} failed: {type(exc).__name__}: {exc}")
                self._json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
            return
        if route != "/turn":
            self._json(404, {"ok": False, "error": "not found"})
            return

        # The body is one turn of text; anything larger is not a turn.
        request = self._read(TurnRequest, MAX_TURN_CHARS * 4)
        if request is None:
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        records = self.server.runner.stream(request)
        try:
            for record in records:
                self.wfile.write(record.model_dump_json().encode("utf-8") + b"\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            # Client hung up. The turn keeps running to completion on the loop
            # thread -- it has already written to memory, and abandoning a
            # half-finished delegation would be worse than finishing it.
            records.close()

    def _read(self, model, max_bytes: int):
        """Validate the JSON body as `model`, or answer 400 and return None."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._json(400, {"ok": False, "error": "bad Content-Length"})
            return None
        if length <= 0 or length > max_bytes:
            self._json(400, {"ok": False, "error": "missing or oversized body"})
            return None
        try:
            return model.model_validate_json(self.rfile.read(length))
        except ValidationError as exc:
            self._json(400, {"ok": False, "error": f"invalid request: {exc}"})
            return None

    def _log_name(self) -> str:
        return f"{self.server.runner.display_name.lower()}-service"

    def _route(self) -> str:
        return self.path.split("?", 1)[0].rstrip("/") or "/"

    def _json(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{self._log_name()}] {fmt % args}")


class _ReceptionistServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, runner: _TurnRunner) -> None:
        self.runner = runner
        super().__init__(address, handler)


def serve(
    settings: Settings,
    role_model: str,
    port: int,
    profile: ReceptionistProfile = TOYOTA,
) -> None:
    name = profile.display_name
    print(f"{name} service -- loading the {settings.embedding_profile} embedder...")
    runner = _TurnRunner.build(settings, role_model, profile=profile)
    server = _ReceptionistServer(("127.0.0.1", port), _ReceptionistHandler, runner)
    print(
        f"{name} service -- http://127.0.0.1:{port}  session: "
        f"{settings.session_id}  model: {settings.model}  "
        f"role_model: {role_model}"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    _load_dotenv()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--agent",
        default=TOYOTA.name,
        choices=sorted(PROFILES),
        help="which receptionist this sidecar serves",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="defaults to the selected receptionist's profile.service_port",
    )
    parser.add_argument(
        "--session",
        default=None,
        help="session id for memory scoping; the profile's default, "
             "shared with the REPL, when omitted",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="override the chat_model on her profile for this run",
    )
    parser.add_argument("--role-model", default="claude-sonnet-5")
    parser.add_argument(
        "--embedding-profile", default="sentence", choices=["hash", "sentence", "openai"]
    )
    args = parser.parse_args()
    profile = named_profile(args.agent)

    settings = Settings(
        session_id=args.session or profile.default_session,
        model=args.model or profile.chat_model,
        embedding_profile=args.embedding_profile,
    )
    # Same archive layout the REPL writes, so a receptionist's nexus/ is one
    # directory whichever front end she answered on.
    settings = settings.model_copy(
        update={"nexus_dir": settings.nexus_dir / profile.nexus_subdir}
    )
    serve(
        settings,
        role_model=args.role_model,
        port=args.port or profile.service_port,
        profile=profile,
    )


if __name__ == "__main__":
    main()
