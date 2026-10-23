"""Boundary models for finance reports and Mickey's progress snapshots."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

Severity = Literal["blocker", "missing", "human_judgment", "ready"]
Verification = Literal["verified", "unknown"]
Status = Literal["not_started", "in_progress", "blocked", "complete"]


class TransactionRecord(BaseModel):
    id: str
    description: str = ""
    amount: float | None = None
    resolved: bool = False


class FinanceReport(BaseModel):
    """On-disk contract for finance_reports/<year>/<month>/<account>.json."""
    year: int
    month: int
    account: str
    transactions: list[TransactionRecord] = []
    generated_at: datetime | None = None
    generated_by: str | None = None
    notes: str | None = None


class AccountPeriodStatus(BaseModel):
    year: int
    month: int
    account: str
    status: Status
    verification: Verification
    completed_work: list[str] = []
    remaining_tasks: list[str] = []
    blockers: list[str] = []
    unresolved_transactions: list[str] = []
    evidence: list[str] = []
    notes: str | None = None


class NextStepCandidate(BaseModel):
    description: str
    responsible_agent: str
    dependencies: list[str] = []
    severity: Severity
    year: int | None = None
    month: int | None = None
    account: str | None = None


class ProgressSnapshot(BaseModel):
    generated_at: datetime
    entries: list[AccountPeriodStatus]
    missing_or_outdated: list[str] = []
    needs_human_judgment: list[str] = []
    next_steps: list[NextStepCandidate] = []
