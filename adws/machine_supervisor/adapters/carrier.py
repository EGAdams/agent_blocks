"""IRemoteAdw and ICarrier: this package's ADWs run on another machine over SSH, and a handoff carried there.

The ADW on the far side is the same file this machine has; a request names a script from this package and its
arguments, never a shell line. Arguments are quoted once, by shlex, for the one remote shell SSH gives us.
"""
from __future__ import annotations

import shlex

from agent_comms.models import Envelope

from ..envelopes import last_json_line
from ..errors import Undelivered
from ..interfaces import IRemoteAdw
from ..models import Machine, Ran
from .runtime import run

SSH_OPTIONS = ("-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10",
               "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4")


def ssh_argv(machine: Machine) -> list[str]:
    return ["ssh", "-p", str(machine.ssh.port), *SSH_OPTIONS, f"{machine.ssh.user}@{machine.ssh.host}"]


class SshRemoteAdw:
    def __init__(self, me: Machine) -> None:
        self._me = me

    def run(self, machine: Machine, script: str, args: list[str], stdin: str | None, timeout: int) -> Ran:
        argv = [machine.python, f"{machine.repo}/adws/{script}", *args]
        if machine.name == self._me.name:
            return run(argv, timeout, stdin=stdin)
        return run([*ssh_argv(machine), shlex.join(argv)], timeout, stdin=stdin if stdin is not None else "")


class SshCarrier:
    """The reply is the last line of the receiving ADW's output that is a JSON object, whatever it exited with:
    a handoff the far side refused still comes back as a (failed) reply."""
    def __init__(self, remote: IRemoteAdw) -> None:
        self._remote = remote

    def carry(self, machine: Machine, envelope: Envelope, timeout: int) -> Envelope:
        ran = self._remote.run(machine, "adw_machine_receive.py", [], envelope.model_dump_json(), timeout)
        line = last_json_line(ran.out)
        if line is None:
            raise Undelivered(f"{machine.name}: {ran.said}")
        try:
            return Envelope.model_validate_json(line)
        except ValueError as error:
            raise Undelivered(f"{machine.name} answered with something that is not a handoff: {error}"[:300]) from None
