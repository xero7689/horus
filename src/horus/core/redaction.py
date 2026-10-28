"""Secret redaction for crawl kwargs.

Adapters take credentials from the environment, never from CLI flags — but Click
accepts unknown `--key value` pairs, so a user can still type
`horus crawl serper --query x --api-key SECRET` and have that land in `crawl_log`
or in a serve job exposed over the web API.

Every path that *records or exposes* kwargs must run them through
`mask_secrets()` first. The kwargs actually handed to the adapter stay untouched.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

MASK = "***"

# Matched as substrings against the normalized key, so `serper_api_key` and
# `Access-Token` are covered too.
_SECRET_HINTS = (
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "credential",
    "cookie",
    "bearer",
    "authorization",
)
# Too short for substring matching (`author`, `keyword`), so one of the key's
# `_`-separated segments must equal the hint exactly, as in `private_key` or
# `basic_auth`. camelCase is split first, so `accessKey` and `HMACKey` count too.
_SECRET_SEGMENTS = frozenset({"key", "auth"})
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _is_secret(key: str) -> bool:
    normalized = _CAMEL_BOUNDARY.sub("_", key).lower().replace("-", "_")
    if any(hint in normalized for hint in _SECRET_HINTS):
        return True
    return not _SECRET_SEGMENTS.isdisjoint(normalized.split("_"))


def mask_secrets(kwargs: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy of `kwargs` with secret-looking values replaced by `MASK`.

    Key matching is case-insensitive and treats `-` and `_` alike. Only key names
    are checked, never values. Mappings nested at any depth, including inside
    lists, are masked too: CLI kwargs are flat strings, but serve's `POST /crawl`
    accepts any JSON value.
    """
    return {key: _mask_value(key, value) for key, value in kwargs.items()}


def _mask_value(key: str, value: Any) -> Any:
    if _is_secret(key):
        return MASK
    return _mask_nested(value)


def _mask_nested(value: Any) -> Any:
    if isinstance(value, Mapping):
        return mask_secrets(value)
    if isinstance(value, list):
        return [_mask_nested(v) for v in value]
    return value
