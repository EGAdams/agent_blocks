# unset ANTHROPIC_API_KEY
# unset ANTHROPIC_BASE_URL
"""Toyota as a long-lived local service, so the dashboard can talk to her.

Same Toyota as `python -m receptionist_team.main` -- the same build_toyota()
composition root, the same LanceDB memory, the same RoutingModel delegation to
finance_team. This module adds no conversational behaviour of its own; it is
an HTTP mouth on the existing agent, so the browser gets the REPL's
experience instead of a second implementation of it.

    cd lancedb_memory
    .venv/bin/python -m receptionist_team.service

WHY A SERVICE AND NOT A SUBPROCESS PER TURN
The `sentence` embedding profile loads a sentence-transformers model (torch)
and opens a LanceDB table. That costs seconds, and a REPL pays it once at
startup -- so the dashboard should too. One warm ConversationAgent therefore
lives here for the process's lifetime, and a turn is just an HTTP request.

WHY IT BINDS LOOPBACK ONLY
A delegated finance turn runs Claude with permission_mode="bypassPermissions"
and real Bash/Read/Write against the live rol_finances MySQL DB (see
finance_team/adapters/tool_agent.py). The dashboard on :8765 is 0.0.0.0-bound
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pydantic import ValidationError

from agent_memory.config import Settings

from .main import _load_dotenv
from .service_models import MAX_TURN_CHARS, TurnRequest
from .turn_runner import _DelegationRelay, _TurnRunner

# 8766 is thought_bridge.py on this box; 8765 is the dashboard itself.
DEFAULT_PORT = 8767

class _ToyotaHandler(BaseHTTPRequestHandler):
    """GET /health, POST /turn, POST /compact. Nothing else, and no static files."""

    server_version = "ToyotaReceptionist/1.0"
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
        if route != "/turn":
            self._json(404, {"ok": False, "error": "not found"})
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._json(400, {"ok": False, "error": "bad Content-Length"})
            return
        # The body is one turn of text; anything larger is not a turn.
        if length <= 0 or length > MAX_TURN_CHARS * 4:
            self._json(400, {"ok": False, "error": "missing or oversized body"})
            return

        try:
            request = TurnRequest.model_validate_json(self.rfile.read(length))
        except ValidationError as exc:
            self._json(400, {"ok": False, "error": f"invalid request: {exc}"})
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
        print(f"[toyota-service] {fmt % args}")


class _ToyotaServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, runner: _TurnRunner) -> None:
        self.runner = runner
        super().__init__(address, handler)


def serve(settings: Settings, role_model: str, port: int) -> None:
    print(f"Toyota service -- loading the {settings.embedding_profile} embedder...")
    runner = _TurnRunner.build(settings, role_model)
    server = _ToyotaServer(("127.0.0.1", port), _ToyotaHandler, runner)
    print(
        f"Toyota service -- http://127.0.0.1:{port}  session: "
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
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--session",
        default="toyota",
        help="session id for memory scoping; shared with the REPL by default",
    )
    parser.add_argument("--model", default="openai/gpt-oss-120b")
    parser.add_argument("--role-model", default="claude-sonnet-5")
    parser.add_argument(
        "--embedding-profile", default="sentence", choices=["hash", "sentence", "openai"]
    )
    args = parser.parse_args()

    settings = Settings(
        session_id=args.session,
        model=args.model,
        embedding_profile=args.embedding_profile,
    )
    settings = settings.model_copy(
        update={"nexus_dir": settings.nexus_dir / "toyota"}
    )
    serve(settings, role_model=args.role_model, port=args.port)


if __name__ == "__main__":
    main()
