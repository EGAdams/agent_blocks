"""Bounded subprocess adapter: fixed catalog argv, no inherited credentials."""
import os
import pwd
import re
import selectors
import signal
import subprocess
import time
from typing import Protocol

from pydantic import BaseModel
from agent_bridge_worker.redaction import SecretScanner
from .commands import CommandSpec, MEMORY

MAX_OUTPUT_BYTES = 48000


class Outcome(BaseModel):
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    truncated: bool = False


class ICommandRunner(Protocol):
    def run(self, spec: CommandSpec) -> Outcome: ...


def redact(text: str) -> str:
    if SecretScanner().found(text) or "PRIVATE KEY-----" in text:
        return "[output withheld: possible credential]"
    return "\n".join("[redacted line]" if re.search(
        r"(?i)password|passwd|token|secret|authorization|bearer|api.?key|cookie|credential|\b[A-Za-z0-9_-]{40,}\b", line)
        else line for line in text.splitlines())


class SubprocessCommandRunner:
    def run(self, spec: CommandSpec) -> Outcome:
        uid = os.getuid()
        env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": pwd.getpwuid(uid).pw_dir,
               "XDG_RUNTIME_DIR": f"/run/user/{uid}",
               "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{uid}/bus",
               "PYTHONDONTWRITEBYTECODE": "1"}
        with subprocess.Popen(list(spec.argv), shell=False, cwd=MEMORY, env=env,
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, start_new_session=True) as child:
            output = {"stdout": bytearray(), "stderr": bytearray()}
            timed_out = truncated = False
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ, "stdout")
                selector.register(child.stderr, selectors.EVENT_READ, "stderr")
                deadline = time.monotonic() + spec.timeout_seconds
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        timed_out = True
                        break
                    for key, _ in selector.select(min(remaining, .1)):
                        chunk = os.read(key.fileobj.fileno(), 4096)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        budget = MAX_OUTPUT_BYTES - sum(map(len, output.values()))
                        output[key.data].extend(chunk[:budget])
                        if len(chunk) > budget:
                            truncated = True
                            break
                    if truncated:
                        break
                if timed_out or truncated:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                try:
                    child.wait(timeout=max(.1, deadline - time.monotonic()) if not (timed_out or truncated) else 5)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    child.wait(timeout=5)
            return Outcome(returncode=child.returncode,
                           stdout=redact(output["stdout"].decode("utf-8", "replace")),
                           stderr=redact(output["stderr"].decode("utf-8", "replace")),
                           timed_out=timed_out, truncated=truncated)
