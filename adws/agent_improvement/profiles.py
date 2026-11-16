"""Target profiles: the only place that says which agent is improved, where, and judged by what.

    sydney   her real engine through `receptionist_team.improvement_probe`, judged by BridgeTaskEvaluator,
             trained on the machine-ops request schema; environments "pilot" (a staged tree, a standing
             policy may approve) and "live" (her real prompt file, then her service restarted; only EG)
    mazda    her finance evaluator from the existing RSI framework (RuleBasedFinanceEvaluator). Her worker
             and her prompt live in Letta on another machine and are not wired here yet, so building her
             profile with an environment says so instead of pretending (see DESIGN.md, "Mazda").

The engine never reads a name from here: `build_profile( name )` hands it a TargetProfile and that is all.
Paths come from the environment so a test or another machine can point elsewhere:

    AGENT_IMPROVEMENT_LIVE_TREE     ~/agent_blocks
    AGENT_IMPROVEMENT_PILOT_TREE    ~/staging/rsi_pilot/agent_blocks
    AGENT_IMPROVEMENT_DATA          ~/staging/rsi_pilot/data   (pilot) / ~/.local/share/agent_improvement (live)
    AGENT_IMPROVEMENT_PYTHON        <live tree>/lancedb_memory/.venv/bin/python
    AGENT_IMPROVEMENT_ENV_FILE      <live tree>/lancedb_memory/receptionist_team/.env  (read by the child only)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from . import rsi
from .engine import TargetProfile
from .gates import EnvironmentPromotionPolicy
from .models import Budgets
from .patching import SectionChangePolicy
from .store import RsiStore
from .sydney import ( PLACEHOLDERS, PROMPT_RELPATH, SECTION_END, SECTION_START, SUITE_VERSION, BridgeTaskEvaluator,
                      ProbeAgentRunner, PromptFileEnvironment, ReferenceEvidence, StaticTaskCatalog, sydney_tasks )

HERE = Path( __file__ ).resolve().parent

#: The people who may approve an activation by hand. A standing policy exists only for the pilot.
APPROVERS = frozenset({ "EG" })
PILOT_POLICY = "pilot-environment-policy"


def _path( name: str, default: Path ) -> Path:
    return Path( os.environ.get( name, str( default ))).expanduser()


def live_tree() -> Path:
    return _path( "AGENT_IMPROVEMENT_LIVE_TREE", Path.home() / "agent_blocks" )


def pilot_tree() -> Path:
    return _path( "AGENT_IMPROVEMENT_PILOT_TREE", Path.home() / "staging" / "rsi_pilot" / "agent_blocks" )


def data_dir( environment: str ) -> Path:
    default = Path.home() / "staging" / "rsi_pilot" / "data" if environment == "pilot" \
        else Path.home() / ".local" / "share" / "agent_improvement"
    return _path( "AGENT_IMPROVEMENT_DATA", default )


def _python() -> str:
    return str( _path( "AGENT_IMPROVEMENT_PYTHON", live_tree() / "lancedb_memory" / ".venv" / "bin" / "python" ))


def _env_file() -> str:
    return str( _path( "AGENT_IMPROVEMENT_ENV_FILE", live_tree() / "lancedb_memory" / "receptionist_team" / ".env" ))


def _probe_argv() -> list[ str ]:
    return [ _python(), "-B", "-m", "receptionist_team.improvement_probe", "--env-file", _env_file()]


# --------------------------------------------------------------------------- sydney

def sydney_profile() -> TargetProfile:
    catalog  = StaticTaskCatalog( SUITE_VERSION, sydney_tasks())
    budgets  = Budgets()
    # The worker always runs the staged tree's code: only the prompt text differs between arms, and it is
    # passed in, so an experiment never needs (or touches) the live tree.
    worker_cwd = pilot_tree() / "lancedb_memory"

    def runner( store: RsiStore, work_root: Path ) -> Any:
        return ProbeAgentRunner( agent="sydney", store=store, argv=_probe_argv(), cwd=worker_cwd, work_root=work_root,
                                 wait_seconds=90.0, timeout_seconds=budgets.worker_seconds )

    def environment( name: str, tree: Path, reload: list[ str ] | None, requires_person: bool ) -> Callable[[], Any ]:
        return lambda: PromptFileEnvironment( name=name, agent="sydney", path=tree / PROMPT_RELPATH,
                                              loaded_check=_probe_argv(), cwd=tree / "lancedb_memory",
                                              reload=reload, requires_person=requires_person )

    return TargetProfile(
        name="sydney",
        model="groq:openai/gpt-oss-120b",
        catalog=catalog,
        evaluator=BridgeTaskEvaluator( catalog ),
        judge_sources=[ HERE / "sydney.py", HERE / "gates.py", HERE / "patching.py", HERE / "engine.py" ],
        evidence=ReferenceEvidence([
            ( "machine_ops/commands.py", live_tree() / "lancedb_memory" / "agent_comms" / "machine_ops" / "commands.py",
              r"UNITS|\.service|class Request|extra=|operation:|unit:|lines:|suite:" ),
        ]),
        change_policy=SectionChangePolicy( SECTION_START, SECTION_END, placeholders=PLACEHOLDERS,
                                           forbidden=( "bypass", "without a yes", "ignore the", "credential", "password" )),
        promotion=EnvironmentPromotionPolicy( standing={ "pilot": PILOT_POLICY },
                                              people={ "pilot": APPROVERS, "live": APPROVERS }),
        environments={
            "pilot": environment( "pilot", pilot_tree(), None, False ),
            "live":  environment( "live", live_tree(),
                                  [ "/usr/bin/systemctl", "--user", "restart", "sydney-receptionist.service" ], True ),
        },
        runner=runner,
        risk_level="medium",
        required_tests=[],
        budgets=budgets,
    )


# --------------------------------------------------------------------------- mazda

class NotWiredEnvironment:
    """IArtifactEnvironment that refuses: Mazda's prompt is in Letta on another machine, unreached from here."""

    requires_person = True

    def __init__( self, name: str, why: str ):
        self.name, self._why = name, why

    def _refuse( self, *_: Any ) -> Any:
        raise RuntimeError( self._why )

    read = write = loaded = _refuse

    def describe( self ) -> str:
        return f"{ self.name }: not wired ({ self._why })"


def mazda_profile() -> TargetProfile:
    """Her own finance judge and the framework's gates, unchanged. Runs only when her runner is wired."""
    from agent_self_improvement.implementations.evaluation.factory import FinanceEvaluationFactory   # noqa: PLC0415

    why = ( "Mazda's system message lives in her Letta agent on Win10 and is read through her existing "
            "reflect/startup wiring; this engine has no runner or environment for it yet" )
    catalog = StaticTaskCatalog( "mazda-finance/unwired", [])

    def runner( store: RsiStore, work_root: Path ) -> Any:
        raise RuntimeError( why )

    return TargetProfile(
        name="Mazda", model="letta", catalog=catalog,
        evaluator=FinanceEvaluationFactory().create_evaluator(),
        judge_sources=[ HERE / "gates.py", HERE / "engine.py" ],
        evidence=ReferenceEvidence([]),
        change_policy=SectionChangePolicy( "\n", "\0" ),
        promotion=EnvironmentPromotionPolicy( standing={}, people={ "live": APPROVERS }),
        environments={ "live": lambda: NotWiredEnvironment( "live", why )},
        runner=runner, risk_level="medium", budgets=Budgets(),
    )


PROFILES: dict[ str, Callable[[], TargetProfile ]] = { "sydney": sydney_profile, "mazda": mazda_profile }


def build_profile( name: str ) -> TargetProfile:
    if name not in PROFILES:
        raise KeyError( f"no improvement target { name !r}; known: { ', '.join( sorted( PROFILES ))}" )
    return PROFILES[ name ]()


# Imported for its side effect of putting the framework on sys.path before mazda_profile's local import.
_ = rsi
