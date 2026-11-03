"""Client-side configuration: where the Bridge is, which route Toyota may use, and
where her client credential lives. Kept apart from the worker's config on purpose.

Variable NAMES (never values) this module reads:
- TOYOTA_BRIDGE_URL                 optional; default is the public client API
- TOYOTA_BRIDGE_CLIENT_TOKEN_FILE   path to a file holding the client-role token
- TOYOTA_BRIDGE_CLIENT_TOKEN        the token itself (prefer the _FILE form)

BRIDGE_WORKER_TOKEN / BRIDGE_WORKER_TOKEN_FILE are the worker's and are never
read here: a worker token is not a client credential.
"""
import json
import os
from collections.abc import Mapping
from pathlib import Path

from .errors import BridgeUnavailable
from .models import BridgeClientConfig, BridgePolicy

ROUTE_CONFIG = Path(__file__).with_name("toyota_bridge_route.json")
URL_VAR = "TOYOTA_BRIDGE_URL"
TOKEN_VAR = "TOYOTA_BRIDGE_CLIENT_TOKEN"
TOKEN_FILE_VAR = "TOYOTA_BRIDGE_CLIENT_TOKEN_FILE"
TOKEN_LIMIT = 4096


def load_policy(path: Path = ROUTE_CONFIG) -> BridgePolicy:
    """What Toyota may ask the Bridge for. Fails loud on implement or a bad name."""
    return BridgePolicy.model_validate(json.loads(path.read_text(encoding="utf-8")))


def load_client_config(env: Mapping[str, str] | None = None) -> BridgeClientConfig:
    env = os.environ if env is None else env
    url = env.get(URL_VAR, "").strip()
    try:
        return BridgeClientConfig(base_url=url) if url else BridgeClientConfig()
    except ValueError as exc:
        raise BridgeUnavailable("bad_config", f"{URL_VAR} is not an allowed Bridge URL") from exc


def load_client_token(env: Mapping[str, str] | None = None) -> str:
    """The client-role token, or BridgeUnavailable. Never logged, never returned in text."""
    env = os.environ if env is None else env
    token = env.get(TOKEN_VAR, "").strip()
    token_file = env.get(TOKEN_FILE_VAR, "").strip()
    if not token and token_file:
        path = Path(token_file).expanduser()
        try:
            token = path.read_text(encoding="utf-8")[:TOKEN_LIMIT].strip()
        except OSError as exc:
            raise BridgeUnavailable("no_credential", f"{TOKEN_FILE_VAR} points at a file that cannot be read") from exc
    if not token:
        raise BridgeUnavailable("no_credential", "no client credential is configured")
    if any(ch.isspace() for ch in token) or len(token) >= TOKEN_LIMIT:
        raise BridgeUnavailable("bad_credential", "the configured client credential is not a single token")
    return token
