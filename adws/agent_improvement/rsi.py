"""The one module that names the existing RSI framework (`agent_self_improvement`).

The framework lives in rol_finances and is reused as it stands: its experiment
runner, gates, activation service, rollback service, approval gateway and SQLite
repositories. Nothing here copies them. Everything else in this package imports
the names below, so moving or vendoring the framework is a change to this file.

    AGENT_IMPROVEMENT_RSI_ROOT   the directory that holds `agent_self_improvement/`
                                 (default ~/rol_finances/tools/self_improving_agent)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

RSI_ROOT = Path( os.environ.get( "AGENT_IMPROVEMENT_RSI_ROOT",
                                 str( Path.home() / "rol_finances" / "tools" / "self_improving_agent" )))
if not ( RSI_ROOT / "agent_self_improvement" ).is_dir():
    raise ImportError( f"the RSI framework is not at { RSI_ROOT }; set AGENT_IMPROVEMENT_RSI_ROOT" )
if str( RSI_ROOT ) not in sys.path:
    sys.path.insert( 0, str( RSI_ROOT ))

from agent_self_improvement.contracts.agent_runner import IAgentRunner                              # noqa: E402
from agent_self_improvement.contracts.enums import ( AgentRuntimeKind, ArtifactType, ExperimentArm,  # noqa: E402
                                                     FailureType, GateDecision, ProposalStatus,
                                                     ProposalType, RiskLevel )
from agent_self_improvement.contracts.evaluation import IEvaluator, IGate, IGateChain               # noqa: E402
from agent_self_improvement.contracts.models import ( ABRunConfig, ActivationResult, ApprovalRecord,  # noqa: E402
                                                      ArtifactChange, ChangeSet, ExperimentResult,
                                                      GateChainResult, GateResult, ImprovementProposal,
                                                      PromptArtifact, Rationale, RunRequest, RunResult,
                                                      ScoreCard, TokenUsage, ToolCallRecord, TraceRecord,
                                                      WrapperRevision )
from agent_self_improvement.implementations.evaluation.gates import ( CostGate, GateChain,          # noqa: E402
                                                                      RegressionGate, SafetyGate,
                                                                      UsefulnessGate )
from agent_self_improvement.implementations.improvement.activation import ActivationService         # noqa: E402
from agent_self_improvement.implementations.improvement.experiments import (                        # noqa: E402
    BaselineCandidateExperimentRunner )
from agent_self_improvement.implementations.sqlite_persistence.approval_gateway import (            # noqa: E402
    SqliteHumanApprovalGateway )
from agent_self_improvement.implementations.sqlite_persistence.improvement_repository import (      # noqa: E402
    SqliteImprovementRepository )
from agent_self_improvement.implementations.sqlite_persistence.prompt_artifact_repository import (  # noqa: E402
    SqlitePromptArtifactRepository )
from agent_self_improvement.implementations.sqlite_persistence.rollback_service import RollbackService  # noqa: E402
from agent_self_improvement.implementations.sqlite_persistence.schema_manager import SqliteSchemaManager  # noqa: E402
from agent_self_improvement.implementations.sqlite_persistence.snapshot_store import SqliteSnapshotStore  # noqa: E402
from agent_self_improvement.implementations.sqlite_persistence.trace_repository import SqliteTraceRepository  # noqa: E402
from agent_self_improvement.implementations.sqlite_persistence.wrapper_revision_repository import (  # noqa: E402
    SqliteWrapperRevisionRepository )

__all__ = [ name for name in dir() if name[ 0 ].isupper() ]
