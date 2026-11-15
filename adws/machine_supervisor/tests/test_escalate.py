from machine_supervisor.models import Incident
from machine_supervisor.operations.escalate import Escalation

from .fakes import Directory, FakeAgent, FakeAlerter, FakeClock, FakeLock, FakeMailer, FakeProbe, Log, Stores, catalog


def build(tmp_path, agents, failing=("unit",)):
    cat, s, probe = catalog(tmp_path), Stores(tmp_path), FakeProbe(set(failing))
    alerter, mailer, clock, log = FakeAlerter(), FakeMailer(), FakeClock(), Log()
    made = {name: agent(probe) for name, agent in agents.items()}
    escalation = Escalation(cat, probe, s.incidents, Directory(made), alerter, mailer, s.state, FakeLock(), clock, log)
    me = cat.me()
    checks = [c for c in me.checks if c.name in failing]
    incident = Incident(id="20261114T120000Z-bati-abc123", machine="BATI", opened_at=clock.now(), checks=checks,
                        failures=[probe.look(c) for c in checks])
    s.incidents.save(incident)
    return escalation, incident, made, alerter, mailer, s


def healer(name):
    return lambda probe: FakeAgent(name, fixes=probe.failing.clear)


def helpless(name, reached=True):
    return lambda probe: FakeAgent(name, reached=reached)


def test_first_agent_fixes_it_one_mail_no_alert(tmp_path):
    escalation, incident, made, alerter, mailer, _ = build(
        tmp_path, {"BATI": healer("BATI"), "SQMC": helpless("SQMC"), "R46": helpless("R46")})
    done = escalation.run(incident.id)
    assert done.status == "fixed" and len(done.attempts) == 1 and made["SQMC"].seen == []
    assert alerter.alerts == [] and len(mailer.mails) == 1
    assert "failed and was fixed" in mailer.mails[0].subject and "restarted x" in mailer.mails[0].body


def test_second_agent_is_woken_with_an_alert_when_the_first_cannot(tmp_path):
    escalation, incident, made, alerter, mailer, _ = build(
        tmp_path, {"BATI": helpless("BATI"), "SQMC": healer("SQMC"), "R46": helpless("R46")})
    done = escalation.run(incident.id)
    assert done.status == "fixed" and [a.machine for a in done.attempts] == ["BATI", "SQMC"]
    assert made["SQMC"].seen == [(incident.id, "helper")] and made["R46"].seen == []
    assert "Waking SQMC" in alerter.alerts[0].message and "is fixed" in alerter.alerts[-1].title


def test_nobody_fixes_it_high_priority_mail_with_a_prompt_and_a_cooldown(tmp_path):
    escalation, incident, made, alerter, mailer, s = build(
        tmp_path, {"BATI": helpless("BATI"), "SQMC": helpless("SQMC", reached=False), "R46": helpless("R46")})
    done = escalation.run(incident.id)
    assert done.status == "unresolved" and [a.machine for a in done.attempts] == ["BATI", "SQMC", "R46"]
    mail = mailer.mails[0]
    assert mail.subject.startswith("HIGH PRIORITY") and "PROMPT FOR A MORE CAPABLE AGENT" in mail.body
    assert "could not be woken" in mail.body or "ssh: no route" in mail.body
    assert "NOT fixed" in alerter.alerts[-1].title and len(alerter.alerts) == 3
    assert s.state.load().unresolved["unit"].incident == incident.id
    assert s.incidents.get(incident.id).status == "unresolved"


def test_already_passing_when_the_supervisor_wakes_no_agent_runs(tmp_path):
    escalation, incident, made, alerter, mailer, _ = build(
        tmp_path, {"BATI": helpless("BATI"), "SQMC": helpless("SQMC"), "R46": helpless("R46")})
    escalation._probe.failing.clear()
    done = escalation.run(incident.id)
    assert done.status == "fixed" and done.attempts == [] and made["BATI"].seen == []
    assert "recovered by itself" in mailer.mails[0].subject


def test_a_closed_incident_is_not_run_twice(tmp_path):
    escalation, incident, made, _, mailer, _ = build(
        tmp_path, {"BATI": healer("BATI"), "SQMC": helpless("SQMC"), "R46": helpless("R46")})
    escalation.run(incident.id)
    escalation.run(incident.id)
    assert len(made["BATI"].seen) == 1 and len(mailer.mails) == 1


def test_a_drill_climbs_every_rung_and_writes_no_cooldown(tmp_path):
    escalation, _, made, alerter, mailer, s = build(
        tmp_path, {"BATI": helpless("BATI"), "SQMC": helpless("SQMC"), "R46": helpless("R46")}, failing=("drill",))
    done = escalation.drill()
    assert done.drill and done.status == "unresolved" and len(done.attempts) == 3
    assert mailer.mails[0].subject.startswith("[DRILL] HIGH PRIORITY") and alerter.alerts[0].title.startswith("[DRILL]")
    assert s.state.load().unresolved == {}
