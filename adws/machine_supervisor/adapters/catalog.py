"""IMachineCatalog over machines.json. Which machine this is comes from its hostname, never from a flag default."""
from __future__ import annotations

import json
import socket
from pathlib import Path

from ..models import Machine, Policy


class JsonMachineCatalog:
    def __init__(self, path: Path, me: str | None = None) -> None:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self._policy = Policy.model_validate(data.get("policy", {}))
        self._machines = {m.name: m for m in (Machine.model_validate(item) for item in data["machines"])}
        self._me = me or self._by_hostname(socket.gethostname())

    def _by_hostname(self, hostname: str) -> str:
        for machine in self._machines.values():
            if machine.hostname.lower() == hostname.lower():
                return machine.name
        raise KeyError(f"this machine ({hostname}) is not in the machine table; known: {', '.join(self._machines)}")

    def me(self) -> Machine:
        return self.get(self._me)

    def get(self, name: str) -> Machine:
        try:
            return self._machines[name.upper()]
        except KeyError:
            raise KeyError(f"no machine named {name}; known: {', '.join(self._machines)}") from None

    def by_agent(self, agent: str) -> Machine | None:
        return next((m for m in self._machines.values() if m.agent == agent), None)

    def peers(self) -> list[Machine]:
        return [self.get(name) for name in self.me().peers]

    def policy(self) -> Policy:
        return self._policy
