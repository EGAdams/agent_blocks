"""IProjectCatalog over projects.json. A project is data; adding one is an entry there, not code."""
from __future__ import annotations

import json
from pathlib import Path

from ..models import Catalog, Project


class JsonProjectCatalog:
    def __init__(self, path: Path) -> None:
        catalog = Catalog.model_validate(json.loads(path.read_text(encoding="utf-8")))
        self._projects = {p.name: p for p in catalog.projects}

    def names(self) -> list[str]:
        return list(self._projects)

    def get(self, name: str) -> Project:
        return self._projects[name]
