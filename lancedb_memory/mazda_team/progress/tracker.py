"""Deterministic collection of Michi's finance-report progress; no LLM calls."""
import os
from datetime import date, datetime, timezone
from pathlib import Path

from .schema import AccountPeriodStatus, FinanceReport, NextStepCandidate, ProgressSnapshot

FINANCE_REPORTS_DIRNAME = "finance_reports"
STATE_RELATIVE_PATH = "progress/michi_state.json"
DEFAULT_EXPECTED_ACCOUNTS = ("checking", "savings", "credit_card")
EXPECTED_ACCOUNTS = tuple(
    a.strip() for a in os.environ.get("MAZDA_TEAM_EXPECTED_ACCOUNTS", "").split(",") if a.strip()
) or DEFAULT_EXPECTED_ACCOUNTS
STALE_DAYS = int(os.environ.get("MAZDA_TEAM_STALE_DAYS", "30"))
MONTHS_BACK = int(os.environ.get("MAZDA_TEAM_MONTHS_BACK", "3"))


def finance_reports_dir(repo_root: str) -> Path:
    return Path(repo_root) / FINANCE_REPORTS_DIRNAME


def state_path(repo_root: str) -> Path:
    return Path(repo_root) / STATE_RELATIVE_PATH


def expected_periods(today: date, months_back: int = MONTHS_BACK) -> list[tuple[int, int]]:
    """Return rolling (year, month) pairs, oldest first."""
    index = today.year * 12 + today.month - 1
    return [(value // 12, value % 12 + 1)
            for value in range(index - max(0, months_back) + 1, index + 1)]


def report_path(repo_root: str, year: int, month: int, account: str) -> Path:
    return finance_reports_dir(repo_root) / str(year) / f"{month:02d}" / f"{account}.json"


def load_report(path: Path) -> tuple[FinanceReport | None, str | None]:
    """Return (report, error); absent files return (None, None)."""
    try:
        return FinanceReport.model_validate_json(path.read_text()), None
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError) as exc:
        return None, f"Cannot read or validate {path}: {exc}"


def status_for_period(repo_root: str, year: int, month: int, account: str,
                      path: Path | None = None) -> AccountPeriodStatus:
    path = path or report_path(repo_root, year, month, account)
    report, error = load_report(path)
    base = dict(year=year, month=month, account=account)
    if error:
        return AccountPeriodStatus(**base, status="blocked", verification="unknown",
                                   blockers=[error], notes=error, evidence=[str(path)])
    if report is None:
        return AccountPeriodStatus(**base, status="not_started", verification="verified",
                                   remaining_tasks=[f"Produce the {account} finance report for {year}-{month:02d}"],
                                   evidence=[f"{path} does not exist"])
    if (report.year, report.month, report.account) != (year, month, account):
        message = (f"Report at {path} is for ({year}, {month}, {account}) but states "
                   f"({report.year}, {report.month}, {report.account})")
        return AccountPeriodStatus(**base, status="blocked", verification="unknown",
                                   blockers=[message], notes=message, evidence=[str(path)])
    unresolved = [f"{t.id}: {t.description}" for t in report.transactions if not t.resolved]
    if unresolved:
        return AccountPeriodStatus(**base, status="in_progress", verification="verified",
                                   remaining_tasks=[f"Resolve {len(unresolved)} unresolved transaction(s)"],
                                   blockers=unresolved, unresolved_transactions=unresolved,
                                   evidence=[str(path)])
    return AccountPeriodStatus(**base, status="complete", verification="verified",
                               completed_work=[f"Report contains {len(report.transactions)} resolved transaction(s)"],
                               evidence=[str(path)])


def iter_all_report_files(repo_root: str):
    """Yield every report at a numeric year/month path."""
    for path in sorted(finance_reports_dir(repo_root).glob("*/*/*.json")):
        year, month = path.parent.parent.name, path.parent.name
        if year.isdigit() and month.isdigit():
            yield int(year), int(month), path.stem, path


def scan_unexpected(repo_root: str, periods: list[tuple[int, int]], expected_accounts) -> list[AccountPeriodStatus]:
    covered = {(year, month, account) for year, month in periods for account in expected_accounts}
    return [status_for_period(repo_root, year, month, account, path)
            for year, month, account, path in iter_all_report_files(repo_root)
            if (year, month, account) not in covered or path != report_path(repo_root, year, month, account)]


def missing_or_outdated(entries: list[AccountPeriodStatus], today: date) -> list[str]:
    findings = []
    for entry in entries:
        label = f"{entry.account} {entry.year}-{entry.month:02d}"
        if entry.status == "not_started":
            findings.append(f"{label} report missing")
        elif entry.status == "in_progress":
            try:
                age = (today - datetime.fromtimestamp(Path(entry.evidence[0]).stat().st_mtime,
                                                        tz=timezone.utc).date()).days
                if age > STALE_DAYS:
                    findings.append(f"{label} report is {age} day(s) old and still has unresolved transactions")
            except (IndexError, OSError):
                findings.append(f"{label} report freshness could not be verified")
    return findings


def needs_human_judgment(entries: list[AccountPeriodStatus], expected_accounts) -> list[str]:
    findings = []
    for entry in entries:
        if entry.status == "blocked":
            findings.append(entry.notes or f"{entry.account} {entry.year}-{entry.month:02d} needs review")
        if entry.account not in expected_accounts:
            findings.append(f"{entry.account} {entry.year}-{entry.month:02d} is not in the configured expected list; confirm this account")
    return findings


def next_step_candidates(entries: list[AccountPeriodStatus], expected_accounts) -> list[NextStepCandidate]:
    steps = []
    for entry in entries:
        year, month, account = entry.year, entry.month, entry.account
        label = f"{year}-{month:02d}"
        if entry.status == "not_started":
            steps.append(NextStepCandidate(description=f"Produce the {account} finance report for {label}",
                                           responsible_agent="sheeta", severity="missing",
                                           year=year, month=month, account=account))
        elif entry.status == "in_progress":
            steps.append(NextStepCandidate(description=f"Resolve {len(entry.unresolved_transactions)} unresolved transaction(s) in the {account} report for {label}",
                                           responsible_agent="sheeta", severity="blocker",
                                           year=year, month=month, account=account))
        elif entry.status == "blocked":
            steps.append(NextStepCandidate(description=f"Review the {account} report for {label}: {entry.notes}",
                                           responsible_agent="human", severity="human_judgment",
                                           year=year, month=month, account=account))
    # Require evidence for every account/month, not just the entries that happened to exist.
    for year in sorted({e.year for e in entries if e.account in expected_accounts}):
        verified = {(e.month, e.account) for e in entries if e.year == year and e.status == "complete"
                    and e.verification == "verified"}
        if (expected_accounts
                and all((month, account) in verified for month in range(1, 13)
                        for account in expected_accounts)
                and all(e.status == "complete" and e.verification == "verified"
                        for e in entries if e.year == year and e.account in expected_accounts)):
            steps.append(NextStepCandidate(
                description=f"All {year} account reports are verified complete -- ready for tarita to prepare the {year} tax form",
                responsible_agent="tarita", severity="ready",
                dependencies=[f"sheeta: all {year} reports complete"], year=year))
    ranks = {"blocker": 0, "missing": 1, "human_judgment": 2, "ready": 3}
    return sorted(steps, key=lambda s: (ranks[s.severity], s.year or 0, s.month or 0, s.account or ""))


def collect_progress(repo_root: str, today: date | None = None, expected_accounts=None) -> ProgressSnapshot:
    today = today or date.today()
    accounts = tuple(EXPECTED_ACCOUNTS if expected_accounts is None else expected_accounts)
    periods = expected_periods(today)
    entries = [status_for_period(repo_root, year, month, account)
               for year, month in periods for account in accounts]
    entries.extend(scan_unexpected(repo_root, periods, accounts))
    return ProgressSnapshot(generated_at=datetime.now(timezone.utc), entries=entries,
                            missing_or_outdated=missing_or_outdated(entries, today),
                            needs_human_judgment=needs_human_judgment(entries, accounts),
                            next_steps=next_step_candidates(entries, accounts))


def save_snapshot(snapshot: ProgressSnapshot, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(snapshot.model_dump_json(indent=2))


def load_snapshot(path: Path) -> ProgressSnapshot | None:
    return ProgressSnapshot.model_validate_json(path.read_text()) if path.exists() else None


def collect_and_persist(repo_root: str, today: date | None = None, expected_accounts=None) -> ProgressSnapshot:
    snapshot = collect_progress(repo_root, today, expected_accounts)
    save_snapshot(snapshot, state_path(repo_root))
    return snapshot
