from machine_supervisor.operations.watch import Watch

from .fakes import Counter, FakeClock, FakeLock, FakeProbe, Log, Stores, catalog


def build(tmp_path, failing=()):
    s, probe, lock, waker, flush, todo = Stores(tmp_path), FakeProbe(set(failing)), FakeLock(), Counter(), Counter(), Counter()
    watch = Watch(catalog(tmp_path), probe, s.state, s.incidents, lock, FakeClock(), waker, s.outbox, flush, todo,
                  s.boxes, Log())
    watch.todo = todo
    return watch, s, probe, lock, waker, flush


def test_all_passing_wakes_nobody(tmp_path):
    watch, s, _, _, waker, _ = build(tmp_path)
    outcome = watch.run()
    assert all(r.ok for r in outcome.results) and outcome.incident is None and waker.woken == []


def test_a_check_must_fail_its_threshold_before_the_supervisor_is_woken(tmp_path):
    watch, s, _, _, waker, _ = build(tmp_path, {"web"})          # threshold 2
    assert watch.run().incident is None and waker.woken == []
    second = watch.run()
    assert second.incident is not None and waker.woken == [second.incident]
    incident = s.incidents.get(second.incident)
    assert [f.check for f in incident.failures] == ["web"] and incident.machine == "BATI"


def test_dry_run_saves_nothing_and_wakes_nobody(tmp_path):
    watch, s, _, _, waker, _ = build(tmp_path, {"unit"})
    outcome = watch.run(dry_run=True)
    assert [r.check for r in outcome.results if not r.ok] == ["unit"]
    assert waker.woken == [] and s.state.load().counters == {}


def test_supervisor_already_awake_is_not_woken_again(tmp_path):
    watch, _, _, lock, waker, _ = build(tmp_path, {"unit"})
    lock.taken.add("supervisor")
    outcome = watch.run()
    assert outcome.incident is None and waker.woken == [] and "already awake" in outcome.lines[0]


def test_wake_limit_holds_a_mail_and_leaves_the_check_alone(tmp_path):
    watch, s, probe, _, waker, flush = build(tmp_path, {"unit"})
    for _ in range(3):                                           # max_wakes_per_day is 3 in the fake table
        assert watch.run().incident is not None
    blocked = watch.run()
    assert blocked.incident is None and len(waker.woken) == 3
    assert "keeps failing" in s.outbox.pending()[0][1].subject
    assert "left alone" in watch.run().lines[0]                  # cooldown now
    assert flush.nudges >= 1                                     # held mail is tried


def test_a_check_left_unresolved_that_passes_again_is_reported(tmp_path):
    watch, s, probe, _, _, _ = build(tmp_path, {"unit"})
    for _ in range(4):
        watch.run()
    before = len(s.outbox.pending())
    probe.failing.clear()
    assert "passing again" in " ".join(watch.run().lines)
    assert len(s.outbox.pending()) == before + 1 and s.state.load().unresolved == {}


def test_a_failed_cron_job_reports_through_the_same_rules(tmp_path):
    from machine_supervisor.models import Check, CheckResult
    watch, s, _, _, waker, _ = build(tmp_path)
    check = Check(name="cron-backup", kind="command", argv=["false"], threshold=1)
    outcome = watch.report(check, CheckResult(check="cron-backup", ok=False, detail="exit 1"))
    assert waker.woken == [outcome.incident] and s.incidents.get(outcome.incident).checks[0].argv == ["false"]


def test_a_todo_left_in_process_by_a_dead_worker_starts_the_worker_to_resume_it(tmp_path):
    from machine_supervisor import envelopes
    watch, s, _, lock, _, _ = build(tmp_path)
    todo = envelopes.request("chatgpt-bridge", "supervisor-bati", "todo", "x", {}, 50, FakeClock().now())
    s.boxes.receive(todo)
    s.boxes.claim(todo.id)                                   # in_process, and nobody holds the lock
    lock.taken.add("supervisor")
    watch.run()
    assert watch.todo.nudges == 0                            # a live worker holds it: leave it alone
    lock.taken.discard("supervisor")
    outcome = watch.run()
    assert watch.todo.nudges == 1 and "interrupted todo: worker started to resume it" in outcome.lines
