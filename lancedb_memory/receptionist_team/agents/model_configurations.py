"""JsonModelConfigurationSource -- the only class that knows the config is JSON.

Single responsibility: get a validated `ModelConfiguration` out of
`model_confiurations/model.json`. A missing file is not an error (an agent may
be defined before it is configured) and yields the pydantic defaults; a
malformed one fails loud with the path, because a silently-defaulted
`max_turns` or `permission_mode` is the kind of thing that only shows up as a
delegated role behaving oddly minutes later.

YAML is deliberately not supported here. `sssf.config.yaml` is the factory's
format and `adw_modules.agents` owns reading it; this is agent-local data and
json is in the stdlib.
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from .models import ModelConfiguration


class InvalidModelConfiguration( RuntimeError ):
    """`model.json` exists but does not describe a ModelConfiguration."""


class JsonModelConfigurationSource:
    """IModelConfigurationSource over one model.json path."""

    def __init__( self, path: Path ) -> None:
        self._path = path

    @property
    def path( self ) -> Path:
        return self._path

    def configuration( self ) -> ModelConfiguration:
        if not self._path.is_file():
            return ModelConfiguration()
        try:
            payload = json.loads( self._path.read_text( encoding="utf-8" ))
        except json.JSONDecodeError as error:
            raise InvalidModelConfiguration( f"{ self._path }: { error }" ) from error
        try:
            return ModelConfiguration.model_validate( payload )
        except ValidationError as error:
            raise InvalidModelConfiguration( f"{ self._path }: { error }" ) from error
