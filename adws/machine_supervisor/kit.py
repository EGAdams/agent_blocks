"""The Machine Supervisor's only composition root: the one module that names the classes behind the interfaces.

Three environment variables move it, for tests and for a trial beside the real one:
    MACHINE_SUPERVISOR_ROOT      this machine's record   (default ~/.local/share/machine_supervisor)
    MACHINE_SUPERVISOR_MACHINES  the machine table       (default machines.json beside this file)
    MACHINE_SUPERVISOR_SETTINGS  {"email_to": "..."}     (default ~/.config/machine_supervisor/settings.json)
"""
from __future__ import annotations

import os
from pathlib import Path

from .adapters.agent import AgentDirectory, ClaudeCli, LocalAnswerer, LocalRepairAgent, LocalTodoAgent
from .adapters.briefs import FileBriefWriter
from .adapters.carrier import SshCarrier, SshRemoteAdw
from .adapters.catalog import JsonMachineCatalog
from .adapters.handoffs import FileHandoffBoxes
from .adapters.mailer import ChainMailer, GmailConnectorMailer, peer_mailers
from .adapters.probes import CommandProbe, HttpProbe, ProbeRegistry, TcpProbe, UnitProbe
from .adapters.runtime import child_env
from .adapters.stores import (DetachedNudge, DetachedWaker, FileIncidentStore, FileOutbox, FileRunLog, FileWatchState,
                              FlockLock, SystemClock)
from .adapters.toast import PowerShellToast
from .adapters.usage_limit import ClaudeUsageLimit
from .operations.alerting import AllMachinesAlerter
from .operations.courier import Courier
from .operations.escalate import Escalation
from .operations.handlers import AlertHandler, AskHandler, HelpHandler, MailHandler
from .operations.mailbox import Mailboxes
from .operations.receive import Receiver
from .operations.todo import TodoPoster, TodoWorker
from .operations.watch import Watch

_PACKAGE = Path(__file__).resolve().parent
_ADWS = _PACKAGE.parent


class Kit:
    def __init__(self, me: str | None = None) -> None:
        root = Path(os.environ.get("MACHINE_SUPERVISOR_ROOT") or Path.home() / ".local/share/machine_supervisor")
        table = Path(os.environ.get("MACHINE_SUPERVISOR_MACHINES") or _PACKAGE / "machines.json")
        settings = Path(os.environ.get("MACHINE_SUPERVISOR_SETTINGS")
                        or Path.home() / ".config/machine_supervisor/settings.json")
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root = root
        self.catalog = JsonMachineCatalog(table, me)
        machine, policy = self.catalog.me(), self.catalog.policy()
        self.clock = SystemClock()
        self.log = FileRunLog(root / "run.log")
        self.lock = FlockLock(root / "locks")
        self.state = FileWatchState(root / "watch_state.json")
        self.incidents = FileIncidentStore(root / "incidents")
        self.outbox = FileOutbox(root / "mail_outbox")
        self.boxes = FileHandoffBoxes(root / "handoffs")
        self.probe = (ProbeRegistry().register("http", HttpProbe()).register("tcp", TcpProbe())
                      .register("unit", UnitProbe()).register("command", CommandProbe()))

        self.remote = SshRemoteAdw(machine)
        self.courier = Courier(self.catalog, self.boxes, SshCarrier(self.remote), self.clock)

        briefs = FileBriefWriter(self.catalog, _PACKAGE / "prompts")
        cli = ClaudeCli(machine, policy, _PACKAGE / "guards" / "bash_guard.py")
        local = LocalRepairAgent(cli, briefs)
        self.agents = AgentDirectory(machine, local, self.courier, policy)

        self.toast = PowerShellToast()
        self.alerter = AllMachinesAlerter(self.catalog, self.toast, self.courier)
        self.gmail = GmailConnectorMailer(machine, policy, settings, _PACKAGE / "guards" / "mail_guard.py")
        self.mailer = ChainMailer([self.gmail, *peer_mailers(self.catalog, self.courier)], self.outbox)

        env, background = child_env(), root / "background.log"
        script = lambda name: str(_ADWS / name)                                   # noqa: E731
        work_todos = DetachedNudge([machine.python, script("adw_machine_todo.py"), "--work"], background, env)
        flush_mail = DetachedNudge([machine.python, script("adw_machine_mail.py"), "--flush"], background, env)
        waker = DetachedWaker(machine.python, _ADWS / "adw_machine_supervise.py", background, env)

        self.receiver = Receiver(self.catalog, self.boxes, {
            "alert": AlertHandler(self.toast), "help": HelpHandler(local, self.incidents),
            "ask": AskHandler(LocalAnswerer(cli, briefs, policy)), "mail": MailHandler(self.gmail),
        }, work_todos, self.clock, self.log)
        self.todo_poster = TodoPoster(self.catalog, self.courier)
        self.todo_worker = TodoWorker(self.catalog, self.boxes, LocalTodoAgent(cli, briefs), self.lock, self.log,
                                      ClaudeUsageLimit(), self.clock)
        self.watch = Watch(self.catalog, self.probe, self.state, self.incidents, self.lock, self.clock, waker,
                           self.outbox, flush_mail, work_todos, self.boxes, self.log)
        self.escalation = Escalation(self.catalog, self.probe, self.incidents, self.agents, self.alerter, self.mailer,
                                     self.state, self.lock, self.clock, self.log)
        self.mailboxes = Mailboxes(self.catalog, self.boxes, self.remote, self.probe, self.state, self.lock,
                                   self.outbox, self.clock)
