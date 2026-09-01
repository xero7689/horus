"""Secret redaction for crawl kwargs.

Adapters take credentials from the environment, never from CLI flags — but Click
accepts unknown `--key value` pairs, so a user can still type
`horus crawl serper --query x --api-key SECRET` and have that land in `crawl_log`
or in a serve job exposed over the web API.

Every path that *records or exposes* kwargs must run them through
`mask_secrets()` first. The kwargs actually handed to the adapter stay untouched.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

MASK = "***"

# Matched as substrings against the normalized key, so `serper_api_key` and
# `Access-Token` are covered too.
_SECRET_HINTS = ("api_key", "apikey", "token", "secret", "password")


def _is_secret(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(hint in normalized for hint in _SECRET_HINTS)


def mask_secrets(kwargs: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy of `kwargs` with secret-looking values replaced by `MASK`.

    Key matching is case-insensitive and treats `-` and `_` alike. Values are not
    inspected — only key names — and nesting is not traversed, since crawl kwargs
    are a flat `str -> str` mapping.
    """
    return {key: (MASK if _is_secret(key) else value) for key, value in kwargs.items()}
