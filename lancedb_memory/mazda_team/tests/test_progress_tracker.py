"""Offline evidence and persistence checks for Michi."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import json
import os
import tempfile
from datetime import date

from mazda_team.progress import tracker
from mazda_team.progress.schema import AccountPeriodStatus

TODAY = date(2025, 6, 15)


def fixture(root, account="checking", month=6, transactions=None, **overrides):
    path = tracker.report_path(root, 2025, month, account)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dict(year=2025, month=month, account=account, transactions=transactions or [])
    data.update(overrides)
    path.write_text(json.dumps(data))
    return path


async def test_missing_report_is_not_started_with_evidence():
    with tempfile.TemporaryDirectory() as root:
        snap = tracker.collect_progress(root, TODAY)
        assert len(snap.entries) == 3 * len(tracker.EXPECTED_ACCOUNTS)
        assert all(e.status == "not_started" and e.verification == "verified" and e.evidence for e in snap.entries)
        assert len(snap.missing_or_outdated) == len(snap.entries)
        assert len(snap.next_steps) == len(snap.entries)
        assert all(s.severity == "missing" and s.responsible_agent == "sheeta" for s in snap.next_steps)


async def test_all_transactions_resolved_is_complete():
    with tempfile.TemporaryDirectory() as root:
        fixture(root, transactions=[dict(id="1", resolved=True), dict(id="2", resolved=True)])
        entry = tracker.status_for_period(root, 2025, 6, "checking")
        assert entry.status == "complete" and entry.verification == "verified"
        assert entry.completed_work and not entry.blockers and entry.evidence


async def test_unresolved_transaction_is_in_progress_never_complete():
    with tempfile.TemporaryDirectory() as root:
        fixture(root, transactions=[dict(id="1", resolved=True),
                                    dict(id="2", description="Unknown ACH", resolved=False)])
        snap = tracker.collect_progress(root, TODAY)
        entry = next(e for e in snap.entries if e.month == 6 and e.account == "checking")
        assert entry.status == "in_progress" and entry.verification == "verified"
        assert len(entry.unresolved_transactions) == 1 and "Unknown ACH" in entry.blockers[0]
        assert any(s.severity == "blocker" and s.responsible_agent == "sheeta" for s in snap.next_steps)


async def test_malformed_json_is_blocked_and_unknown():
    with tempfile.TemporaryDirectory() as root:
        path = fixture(root)
        path.write_text("not valid json")
        snap = tracker.collect_progress(root, TODAY)
        entry = next(e for e in snap.entries if e.month == 6 and e.account == "checking")
        assert entry.status == "blocked" and entry.verification == "unknown"
        assert entry.notes in snap.needs_human_judgment


async def test_path_field_mismatch_is_blocked():
    with tempfile.TemporaryDirectory() as root:
        path = fixture(root, month=6)
        data = json.loads(path.read_text())
        data["month"] = 5
        path.write_text(json.dumps(data))
        entry = tracker.status_for_period(root, 2025, 6, "checking")
        assert entry.status == "blocked" and entry.verification == "unknown"
        assert "6" in entry.blockers[0] and "5" in entry.blockers[0]


async def test_unexpected_account_flagged_for_human_review():
    with tempfile.TemporaryDirectory() as root:
        fixture(root, account="joint")
        snap = tracker.collect_progress(root, TODAY, ("checking",))
        assert any(e.account == "joint" for e in snap.entries)
        assert any("joint" in item and "not in the configured expected list" in item
                   for item in snap.needs_human_judgment)


async def test_year_complete_triggers_tarita_ready_step():
    entries = [AccountPeriodStatus(year=2025, month=month, account="checking", status="complete",
                                   verification="verified", evidence=["file"])
               for month in range(1, 13)]
    steps = tracker.next_step_candidates(entries, ("checking",))
    assert len(steps) == 1 and steps[0].responsible_agent == "tarita" and steps[0].severity == "ready"
    assert steps[0].dependencies
    entries[0] = entries[0].model_copy(update={"status": "not_started"})
    assert not any(s.responsible_agent == "tarita" for s in tracker.next_step_candidates(entries, ("checking",)))
    # A partial year is not evidence that the entire year is ready.
    assert not any(s.responsible_agent == "tarita" for s in tracker.next_step_candidates(entries[1:], ("checking",)))


async def test_next_step_priority_ordering():
    entries = [AccountPeriodStatus(year=2025, month=m, account="checking", status=status,
                                   verification="verified", unresolved_transactions=["x"])
               for m, status in ((1, "not_started"), (2, "blocked"), (3, "in_progress"))]
    entries[1].notes = "invalid"
    entries.extend(AccountPeriodStatus(year=2024, month=m, account="checking", status="complete",
                                       verification="verified", evidence=["file"])
                   for m in range(1, 13))
    steps = tracker.next_step_candidates(entries, ("checking",))
    assert [s.severity for s in steps] == ["blocker", "missing", "human_judgment", "ready"]


async def test_persistence_round_trip():
    with tempfile.TemporaryDirectory() as root:
        snap = tracker.collect_and_persist(root, TODAY)
        path = tracker.state_path(root)
        assert path.exists()
        restored = tracker.load_snapshot(path)
        assert restored.model_dump(mode="json") == snap.model_dump(mode="json")


async def test_noncanonical_month_path_is_inspected_not_ignored():
    with tempfile.TemporaryDirectory() as root:
        path = Path(root) / "finance_reports/2024/6/checking.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(dict(year=2024, month=6, account="checking",
                                        transactions=[dict(id="x", resolved=False)])))
        snap = tracker.collect_progress(root, TODAY, ("checking",))
        entry = next(e for e in snap.entries if e.year == 2024 and e.month == 6)
        assert entry.status == "in_progress" and entry.evidence == [str(path)]


async def test_stale_unresolved_report_is_flagged():
    with tempfile.TemporaryDirectory() as root:
        path = fixture(root, transactions=[dict(id="x", resolved=False)])
        old = date(2025, 4, 1).strftime("%s")
        os.utime(path, (int(old), int(old)))
        snap = tracker.collect_progress(root, TODAY)
        assert any("old" in item and "unresolved" in item for item in snap.missing_or_outdated)


async def test_rolling_periods_cross_year_boundary():
    assert tracker.expected_periods(date(2025, 1, 15), 3) == [(2024, 11), (2024, 12), (2025, 1)]


if __name__ == "__main__":
    from mazda_team.tests.runner import main
    main(globals())
