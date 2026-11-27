"""Session ids the server issues and can check (TASK-5.29).

The browser's API key ships in the JavaScript bundle, so it says nothing about
who a caller is. What does is the session id: the server mints it as
``<random hex>.<HMAC-SHA256 of that hex>`` under ``SESSION_SECRET`` and hands it
to the browser in the ``done`` event. Holding a verified id is what lets a
caller read that session's runs and patches; an id the server did not mint is
refused, so a caller cannot adopt someone else's session by guessing or by
copying an id from elsewhere.

``SESSION_SECRET`` may list several secrets separated by commas: new ids are
signed with the first and any of them verifies, so a secret can be rotated
without ending live sessions. Without it, a random secret is made per process,
which is fine for local work but ends every session on restart.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
from functools import lru_cache

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[0-9a-f]{32}\.[0-9a-f]{64}")


@lru_cache(maxsize=1)
def _secrets() -> tuple[bytes, ...]:
    configured = [
        s.strip() for s in os.environ.get("SESSION_SECRET", "").split(",") if s.strip()
    ]
    if configured:
        return tuple(s.encode() for s in configured)
    logger.warning(
        "SESSION_SECRET is not set; using a per-process secret, so sessions end on restart"
    )
    return (secrets.token_bytes(32),)


def reset_secrets() -> None:
    """Re-read ``SESSION_SECRET`` (tests, and after changing the environment)."""
    _secrets.cache_clear()


def _sign(nonce: str, key: bytes) -> str:
    return hmac.new(key, nonce.encode(), hashlib.sha256).hexdigest()


def mint_session_id() -> str:
    """A new session id that only this server's secret could have produced."""
    nonce = secrets.token_hex(16)
    return f"{nonce}.{_sign(nonce, _secrets()[0])}"


def is_valid_session_id(session_id: str | None) -> bool:
    """True only for an id minted under one of the configured secrets."""
    if not session_id or not _TOKEN_RE.fullmatch(session_id):
        return False
    nonce, signature = session_id.split(".", 1)
    return any(hmac.compare_digest(_sign(nonce, key), signature) for key in _secrets())
