"""Boundary models for finance orchestration outcomes and dashboard callbacks."""

from pydantic import BaseModel


class OrchestrationResult( BaseModel ):
    """The outcome of running one document through the finance orchestrator."""

    document_id: str
    stored: bool


class DashboardCallback( BaseModel ):
    """The callback always sent to the dashboard, success or failure."""

    document_id: str
    already_stored: bool
