"""One isolated receptionist turn under a given system-prompt revision, as JSON.

The worker half of an improvement experiment (see adws/agent_improvement/DESIGN.md).
The improvement cycle's experiment runs a baseline prompt revision and a
candidate one over the same task; each run is one of these processes:

    echo '{"agent": "sydney", "system_prompt_template": "...", "input_text": "...",
           "work_dir": "/tmp/x"}' | .venv/bin/python -m receptionist_team.improvement_probe

What is real: her engine (build_receptionist), her roster, her forced routes,
her Groq chat model, and -- for the read-only `star_trek_bridge` route only --
her own Bridge credential and sender, so a task she submits is a real task.

What is isolated: her memory and nexus (a fresh table under `work_dir`, so
neither arm recalls the live conversation or writes into it), her completion
notices (none: live Sydney is never told about an experiment's task), and
every other route. A route other than `star_trek_bridge` is recorded and NOT
run -- an experiment must never open a browser, post mail or change a file.

This module judges nothing. It reports what happened -- the model's own words,
the route it chose, the Bridge workflow's phase records -- and the evaluator,
which the agent under test cannot edit, decides what that was worth.

It is a composition root: the one place here allowed to name concretes.
"""
import argparse
import asyncio
import hashlib
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

from agent_memory.config import Settings
from agent_memory.models import AgentReply

from .agents.prompt_templates import TextPromptTemplate
from .delegation import IDelegationRunner

#: The only route an experiment turn is allowed to run for real.
LIVE_ROUTE = "star_trek_bridge"


class RecordingRunner:
    """IDelegationRunner: runs the one permitted route, records every other one unrun."""

    def __init__(self, live: dict[str, IDelegationRunner]) -> None:
        self._live = dict(live)
        self.delegations: list[dict] = []

    async def run(self, role: object, agent_name: str, model: str, extra: str = "") -> AgentReply:
        runner = self._live.get(agent_name)
        self.delegations.append({"agent": agent_name, "extra": extra, "ran": runner is not None})
        if runner is None:
            return AgentReply(text=f"[experiment: route {agent_name} was chosen and deliberately not run]")
        return await runner.run(role, agent_name, model, extra)


def _load_env_file(path: str) -> None:
    """KEY=VALUE lines into os.environ, never overriding and never echoed."""
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _phase(record) -> dict:
    return {"name": record.name, "status": record.status, "inputs": dict(record.inputs),
            "outputs": dict(record.outputs), "error": record.error}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _loaded(request: dict) -> dict:
    """What her real profile loads from this tree, beside the rendering of the revision expected there.

    No model call and no Bridge: this is how an activation or a rollback is verified -- by the prompt
    the profile module actually built at import, not by the file the engine believes it wrote.
    """
    from .profiles import profile as named_profile

    profile = named_profile(request["agent"])
    expected = TextPromptTemplate(request["system_prompt_template"], origin="<expected revision>").render(
        {"roster": profile.roster.roster_text(), "allowed_agents": profile.roster.allowed_agents()}
    )
    return {"ok": True, "agent": profile.name, "loaded_sha256": _sha(profile.system_prompt),
            "expected_sha256": _sha(expected), "matches": profile.system_prompt == expected}


async def _turn(request: dict) -> dict:
    # Imported here so --help and a bad request never build a receptionist.
    from .adapters.bridge_runner import BridgeDelegationRunner
    from .bridge_client.desk import desk_for
    from .main import FALLBACK_KEY_VARS, build_receptionist
    from .profiles import profile as named_profile

    for name in FALLBACK_KEY_VARS:
        # Groq only: a baseline answered by one provider and a candidate by
        # another is not a comparison. A rate limit is reported as an error.
        os.environ.pop(name, None)

    profile = named_profile(request["agent"])
    desk = desk_for(profile.name)
    bridge = BridgeDelegationRunner(
        client_factory=desk.client_factory, key_factory=desk.new_key,
        wait_seconds=float(request.get("wait_seconds", 0.0)), poll_seconds=5.0,
    )
    runner = RecordingRunner({LIVE_ROUTE: bridge})
    rendered = TextPromptTemplate(request["system_prompt_template"], origin="<experiment arm>").render(
        {"roster": profile.roster.roster_text(), "allowed_agents": profile.roster.allowed_agents()}
    )
    arm = replace(profile, system_prompt=rendered, build_runner=lambda: runner,
                  peer_session=None, peer_label=None, extra_peers=())

    work = Path(request["work_dir"])
    work.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        session_id=request.get("session_id", "improvement-probe"),
        model=request.get("model") or profile.chat_model,
        embedding_profile="hash",
        lancedb_uri=work / "lance_data",
        nexus_dir=work / "nexus",
    )
    said: list[str] = []
    conversation, cost = build_receptionist(arm, settings, role_model="unused-in-experiments",
                                            on_reply_delta=said.append)
    reply = await conversation.handle_turn(request["input_text"])
    trace = [_phase(r) for r in bridge.last_trace]
    return {
        "ok": True,
        "agent": profile.name,
        "model": settings.model,
        "reply_text": reply.text,
        "model_text": "".join(said),
        "delegations": runner.delegations,
        "bridge": {"task_id": bridge.last_task_id, "trace": trace},
        "input_tokens": reply.input_tokens or cost.total_input_tokens,
        "output_tokens": reply.output_tokens or cost.total_output_tokens,
        "prompt_chars": len(rendered),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env-file", action="append", default=[],
                        help="KEY=VALUE file holding the model key and Bridge credential names (repeatable)")
    args = parser.parse_args()
    out = sys.stdout
    # Everything the engine prints (console observer, acknowledgments) goes to
    # stderr, so stdout carries exactly one JSON document.
    sys.stdout = sys.stderr
    try:
        request = json.loads(sys.stdin.read())
        for path in args.env_file:
            _load_env_file(path)
        result = _loaded(request) if request.get("report_loaded") else asyncio.run(_turn(request))
    except Exception as exc:  # reported, never raised: the caller reads JSON, not a traceback
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:600]}
    out.write(json.dumps(result) + "\n")
    out.flush()
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
