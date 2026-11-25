"""Short-lived store for binary run outputs (rendered audio).

Events carry an id, not the bytes, so streams stay small. In memory and
per-process, like the response cache: an expired id is simply no longer playable.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass

import cachetools

MAX_TOTAL_BYTES = 64 * 1024 * 1024
TTL_SECONDS = 3600


@dataclass(frozen=True)
class StoredArtifact:
    data: bytes
    mime: str


_store: cachetools.TTLCache = cachetools.TTLCache(
    maxsize=MAX_TOTAL_BYTES, ttl=TTL_SECONDS, getsizeof=lambda a: len(a.data)
)
_lock = threading.Lock()


def put(data: bytes, mime: str) -> str:
    artifact_id = uuid.uuid4().hex
    with _lock:
        _store[artifact_id] = StoredArtifact(data, mime)
    return artifact_id


def get(artifact_id: str) -> StoredArtifact | None:
    with _lock:
        return _store.get(artifact_id)
