"""Placeholder boundary models returned by the ten factory-family interfaces.

Each handle stands in for the real object the factory will eventually build.
They carry no behavior yet; replace with the real boundary model when the
corresponding factory migrates out of the planning stage.
"""

from pydantic import BaseModel


class UnitOfWorkHandle( BaseModel ):
    """Placeholder for IPersistenceFactory's schema/unit-of-work/repositories."""

    handle_id: str


class LlmClientHandle( BaseModel ):
    """Placeholder for ILlmFactory's client and token/budget services."""

    handle_id: str


class PromptSetHandle( BaseModel ):
    """Placeholder for IPromptFactory's composed and validated prompt set."""

    handle_id: str


class ToolRegistryHandle( BaseModel ):
    """Placeholder for IToolFactory's registry, adapters, and permissions."""

    handle_id: str


class ContextBundleHandle( BaseModel ):
    """Placeholder for IContextFactory's selected context, budget, and notes."""

    handle_id: str


class WorkflowRunHandle( BaseModel ):
    """Placeholder for IWorkflowFactory's workflow runner."""

    handle_id: str


class EvaluationVerdictHandle( BaseModel ):
    """Placeholder for IEvaluationFactory's verdict and failure classification."""

    handle_id: str


class ImprovementProposalHandle( BaseModel ):
    """Placeholder for IImprovementFactory's proposal/experiment/rollback state."""

    handle_id: str


class ReportHandle( BaseModel ):
    """Placeholder for IReportingFactory's trace/dashboard/regression report."""

    handle_id: str


class AgentPackageHandle( BaseModel ):
    """Placeholder for IAgentPackageFactory's named agent package."""

    handle_id: str
