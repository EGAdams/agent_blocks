"""The channel end to end, with three Receivers in one process joined by a carrier that skips SSH."""
import json

import pytest
from agent_comms.models import TaskStatus

from machine_supervisor import envelopes
from machine_supervisor.adapters.agent import PeerRepairAgent
from machine_supervisor.adapters.mailer import ChainMailer, PeerMailer
from machine_supervisor.errors import Refused, Undelivered
from machine_supervisor.models import Alert, Incident, Mail
from machine_supervisor.operations.alerting import AllMachinesAlerter
from machine_supervisor.operations.courier import Courier
from machine_supervisor.operations.handlers import AlertHandler, AskHandler, HelpHandler, MailHandler
from machine_supervisor.operations.mailbox import Mailboxes
from machine_supervisor.operations.receive import Receiver
from machine_supervisor.operations.todo import TodoPoster, TodoWorker, queued_todos

from .fakes import (Counter, FakeAgent, FakeClock, FakeLock, FakeMailer, FakeProbe, FakeToast, Log, LoopCarrier,
                    Stores, catalog)


class Site:
    def __init__(self, tmp_path, name, carrier, clock):
        root = tmp_path / name
        root.mkdir()
        self.catalog, self.stores = catalog(root, name), Stores(root)
        self.agent, self.toast, self.gmail, self.nudge = FakeAgent(name), FakeToast(), FakeMailer(via=f"{name} gmail"), Counter()
        self.courier = Courier(self.catalog, self.stores.boxes, carrier, clock)
        self.receiver = Receiver(self.catalog, self.stores.boxes, {
            "alert": AlertHandler(self.toast), "help": HelpHandler(self.agent, self.stores.incidents),
            "ask": AskHandler(self.agent), "mail": MailHandler(self.gmail)}, self.nudge, clock, Log())
        carrier.receivers[name] = self.receiver


@pytest.fixture
def sites(tmp_path):
    carrier, clock = LoopCarrier(), FakeClock()
    made = {name: Site(tmp_path, name, carrier, clock) for name in ("BATI", "SQMC", "R46")}
    made["carrier"] = carrier
    return made


def test_a_handoff_moves_outbox_to_sent_and_inbox_to_completed(sites):
    bati, sqmc = sites["BATI"], sites["SQMC"]
    reply = bati.courier.send(sqmc.catalog.me(), "alert", "body", {"title": "T", "message": "body"}, 30)
    assert reply.outcome is TaskStatus.SUCCEEDED and sqmc.toast.shown[0].title == "T"
    assert len(bati.stores.boxes.box("sent")) == 1 and bati.stores.boxes.box("outbox") == []
    assert [e.task_type for e in sqmc.stores.boxes.box("completed")] == ["alert"]
    assert [e.kind.value for e in bati.stores.boxes.box("completed")] == ["reply"]


def test_an_unreachable_machine_is_undelivered_and_filed_as_such(sites):
    sites["carrier"].down.add("R46")
    bati = sites["BATI"]
    with pytest.raises(Undelivered):
        bati.courier.send(bati.catalog.get("R46"), "alert", "x", {}, 30)
    assert len(bati.stores.boxes.box("undelivered")) == 1


def test_the_same_handoff_twice_is_refused(sites):
    bati, sqmc = sites["BATI"], sites["SQMC"]
    asked = envelopes.request("supervisor-bati", "supervisor-sqmc", "alert", "x", {}, 50, FakeClock().now())
    sqmc.receiver.receive(asked.model_dump_json())
    with pytest.raises(Refused):
        sqmc.receiver.receive(asked.model_dump_json())


def test_only_another_supervisor_may_ask_for_help_alerts_or_mail(sites):
    sqmc = sites["SQMC"]
    for kind in ("alert", "help", "ask", "mail"):
        asked = envelopes.request("chatgpt-bridge", "supervisor-sqmc", kind, "x", {}, 50, FakeClock().now())
        reply = sqmc.receiver.receive(asked.model_dump_json())
        assert reply.outcome is TaskStatus.FAILED and "only from another machine" in reply.error
    assert sqmc.toast.shown == [] and sqmc.gmail.mails == [] and sqmc.agent.seen == []


def test_garbage_and_mail_for_somebody_else_are_refused(sites):
    with pytest.raises(Refused):
        sites["SQMC"].receiver.receive("rm -rf /")
    asked = envelopes.request("supervisor-bati", "supervisor-r46", "alert", "x", {}, 50, FakeClock().now())
    with pytest.raises(Refused):
        sites["SQMC"].receiver.receive(asked.model_dump_json())


def test_help_wakes_the_peer_agent_and_its_report_comes_back(sites, tmp_path):
    bati, sqmc = sites["BATI"], sites["SQMC"]
    me = bati.catalog.me()
    incident = Incident(id="20261114T120000Z-bati-abc123", machine="BATI", opened_at=FakeClock().now(),
                        checks=me.checks[:1], failures=[FakeProbe({"web"}).look(me.checks[0])])
    report = PeerRepairAgent(sqmc.catalog.me(), bati.courier, bati.catalog.policy()).attempt(incident, "helper")
    assert report.machine == "SQMC" and report.role == "helper" and report.reached
    assert sqmc.stores.incidents.get(incident.id).machine == "BATI"       # saved so a question can carry it
    sites["carrier"].down.add("SQMC")
    gone = PeerRepairAgent(sqmc.catalog.me(), bati.courier, bati.catalog.policy()).attempt(incident, "helper")
    assert not gone.reached and "refused" in gone.error


def test_a_question_is_answered_in_the_same_call(sites):
    bati, sqmc = sites["BATI"], sites["SQMC"]
    reply = bati.courier.send(sqmc.catalog.me(), "ask", "is it up?", {}, 30)
    assert reply.result == "SQMC says 42" and sqmc.agent.seen == [("supervisor-bati", "is it up?", None)]


def test_alert_reaches_every_machine_and_says_which_it_missed(sites):
    bati = sites["BATI"]
    sites["carrier"].down.add("R46")
    lines = AllMachinesAlerter(bati.catalog, bati.toast, bati.courier).alert(Alert(title="T", message="M"))
    assert lines[0] == "BATI: shown" and lines[1] == "SQMC: shown" and "R46: NOT shown" in lines[2]


def test_mail_falls_to_a_peer_then_is_held(sites):
    bati = sites["BATI"]
    bati.gmail.sends = False
    chain = ChainMailer([bati.gmail, *[PeerMailer(p, bati.courier, bati.catalog.policy()) for p in bati.catalog.peers()]],
                        bati.stores.outbox)
    assert chain.send(Mail(subject="S", body="B")).via == "SQMC gmail" and sites["SQMC"].gmail.mails[0].subject == "S"
    sites["SQMC"].gmail.sends = sites["R46"].gmail.sends = False
    held = chain.send(Mail(subject="S2", body="B"))
    assert not held.sent and held.via == "outbox" and bati.stores.outbox.pending()[0][1].subject == "S2"


def test_a_todo_is_queued_then_worked_and_readable_from_another_machine(sites):
    bati, sqmc = sites["BATI"], sites["SQMC"]
    ok, line, data = TodoPoster(bati.catalog, bati.courier).post("SQMC", "check the scanner", "chatgpt-bridge", "task-1")
    assert ok and data["machine"] == "SQMC" and sqmc.nudge.nudges == 1 and queued_todos(sqmc.stores.boxes) == 1
    assert sqmc.agent.seen == []                                           # nothing ran yet
    lines = TodoWorker(sqmc.catalog, sqmc.stores.boxes, sqmc.agent, FakeLock(), Log()).work()
    assert "done" in lines[0] and queued_todos(sqmc.stores.boxes) == 0
    box, done = sqmc.stores.boxes.find(data["id"])
    assert box == "completed" and "did: check the scanner" in done.result and done.sender == "chatgpt-bridge"


def test_a_todo_from_a_stranger_is_refused_and_never_worked(sites):
    bati, sqmc = sites["BATI"], sites["SQMC"]
    ok, line, _ = TodoPoster(bati.catalog, bati.courier).post("SQMC", "do a thing", "mallory")
    assert not ok and "may not give" in line and queued_todos(sqmc.stores.boxes) == 0
    assert TodoPoster(bati.catalog, bati.courier).post("MARS", "x", "operator")[0] is False


def test_a_busy_supervisor_leaves_todos_queued(sites):
    bati, sqmc = sites["BATI"], sites["SQMC"]
    TodoPoster(bati.catalog, bati.courier).post("SQMC", "later", "operator")
    lock = FakeLock()
    lock.taken.add("supervisor")
    assert TodoWorker(sqmc.catalog, sqmc.stores.boxes, sqmc.agent, lock, Log()).work() == []
    assert queued_todos(sqmc.stores.boxes) == 1


class Remote:
    """IRemoteAdw that answers as the other machine's adw_machine_mailbox.py would."""
    def __init__(self, sites):
        self.sites, self.calls = sites, []

    def run(self, machine, script, args, stdin, timeout):
        from machine_supervisor.models import Ran
        self.calls.append((machine.name, script, args))
        if machine.name in self.sites["carrier"].down:
            return Ran(code=255, err="ssh: connect to host: Connection refused")
        there = boxes_for(self.sites[machine.name], self)
        ok, line, data = (there.mailbox(machine.name, args[args.index("--box") + 1]) if args[0] == "mailbox"
                          else there.status(machine.name))
        return Ran(code=0, out="adw noise\n" + json.dumps({"ok": ok, "line": line, "data": data}))


def boxes_for(site, remote):
    return Mailboxes(site.catalog, site.stores.boxes, remote, FakeProbe({"unit"}), site.stores.state, FakeLock(),
                     site.stores.outbox, FakeClock())


def test_the_mailbox_reader_reads_here_and_relays_from_there(sites):
    bati, sqmc = sites["BATI"], sites["SQMC"]
    TodoPoster(bati.catalog, bati.courier).post("SQMC", "peek", "operator")
    remote = Remote(sites)
    reader = boxes_for(bati, remote)
    assert reader.machines()[2]["machines"][0]["here"] is True
    ok, line, data = reader.mailbox("SQMC", "new")
    assert ok and data["messages"][0]["text"] == "peek" and remote.calls[0][:2] == ("SQMC", "adw_machine_mailbox.py")
    ok, line, data = reader.status("BATI")
    assert ok and data["failing"] == ["unit"] and "1/2 checks passing" in line
    assert reader.mailbox("BATI")[2]["counts"]["sent"] == 1
    assert reader.mailbox("BATI", "../etc")[0] is False and reader.read("BATI", "../../x")[0] is False
    sites["carrier"].down.add("R46")
    ok, line, _ = reader.status("R46")
    assert not ok and "may be switched off" in line
    with pytest.raises(KeyError):
        reader.status("MARS")
