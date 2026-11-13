"""Client-side configuration: where the Bridge is, which route a receptionist may use,
and where her client credential lives. Kept apart from the worker's config on purpose.

Each receptionist with a Bridge desk has her OWN credential, bound on the gateway to
her own sender (`toyota-bridge`, `sydney-bridge`), so the audit trail says who sent
what. The variable names differ only by prefix (`TOYOTA`, `SYDNEY`).

Variable NAMES (never values) this module reads, for prefix P:
- P_BRIDGE_URL                 optional; default is the public client API
- P_BRIDGE_CLIENT_TOKEN_FILE   path to a file holding the client-role token
- P_BRIDGE_CLIENT_TOKEN        the token itself (prefer the _FILE form)

BRIDGE_WORKER_TOKEN / BRIDGE_WORKER_TOKEN_FILE are the worker's and are never
read here: a worker token is not a client credential.
"""
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path

from .errors import BridgeUnavailable
from .models import BridgeClientConfig, BridgePolicy

ROUTE_CONFIG = Path(__file__).with_name("toyota_bridge_route.json")
URL_VAR = "TOYOTA_BRIDGE_URL"
TOKEN_VAR = "TOYOTA_BRIDGE_CLIENT_TOKEN"
TOKEN_FILE_VAR = "TOYOTA_BRIDGE_CLIENT_TOKEN_FILE"
TOKEN_LIMIT = 4096
DEFAULT_PREFIX = "TOYOTA"


def _names(prefix: str) -> tuple[str, str, str]:
    """(url, token, token-file) variable names for one receptionist's credential."""
    if not re.fullmatch(r"[A-Z][A-Z0-9]{1,15}", prefix or ""):
        raise BridgeUnavailable("bad_config", "a Bridge credential prefix must be a short upper-case name")
    if prefix == DEFAULT_PREFIX:
        return URL_VAR, TOKEN_VAR, TOKEN_FILE_VAR
    return f"{prefix}_BRIDGE_URL", f"{prefix}_BRIDGE_CLIENT_TOKEN", f"{prefix}_BRIDGE_CLIENT_TOKEN_FILE"


def load_policy(path: Path = ROUTE_CONFIG) -> BridgePolicy:
    """What Toyota may ask the Bridge for. Fails loud on implement or a bad name."""
    return BridgePolicy.model_validate(json.loads(path.read_text(encoding="utf-8")))


def load_client_config(env: Mapping[str, str] | None = None, prefix: str = DEFAULT_PREFIX) -> BridgeClientConfig:
    env = os.environ if env is None else env
    url_var, _, _ = _names(prefix)
    url = env.get(url_var, "").strip()
    try:
        return BridgeClientConfig(base_url=url) if url else BridgeClientConfig()
    except ValueError as exc:
        raise BridgeUnavailable("bad_config", f"{url_var} is not an allowed Bridge URL") from exc


def load_client_token(env: Mapping[str, str] | None = None, prefix: str = DEFAULT_PREFIX) -> str:
    """The client-role token, or BridgeUnavailable. Never logged, never returned in text.
    Never falls back to another receptionist's credential: no token of her own means unavailable."""
    env = os.environ if env is None else env
    _, token_var, token_file_var = _names(prefix)
    token = env.get(token_var, "").strip()
    token_file = env.get(token_file_var, "").strip()
    if not token and token_file:
        path = Path(token_file).expanduser()
        try:
            token = path.read_text(encoding="utf-8")[:TOKEN_LIMIT].strip()
        except OSError as exc:
            raise BridgeUnavailable("no_credential", f"{token_file_var} points at a file that cannot be read") from exc
    if not token:
        raise BridgeUnavailable("no_credential", "no client credential is configured")
    if any(ch.isspace() for ch in token) or len(token) >= TOKEN_LIMIT:
        raise BridgeUnavailable("bad_credential", "the configured client credential is not a single token")
    return token
