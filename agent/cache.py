"""In-process response cache, shared by every interface.

Documentation answers are objective, so the cache is keyed on the normalised
question only — not on history. The same question asked mid-session returns the
cached answer instead of paying for retrieval and generation again.

A cached entry also records whether the request extended the history, so a hit
can reconstruct exactly what the miss produced. Tool calls are not replayed on a
hit: the answer and its sources are, and that is what the client sees.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field

import cachetools

from .types import Chunk

CACHE_MAXSIZE = 256
CACHE_TTL_SECONDS = 3600


@dataclass(frozen=True)
class CachedAnswer:
    """One stored response."""

    question: str
    answer: str
    chunks: list[Chunk] = field(default_factory=list)
    query_type: str = ""
    extend_history: bool = True
    patches: tuple[dict, ...] = ()
    audio: tuple[dict, ...] = ()


response_cache: cachetools.TTLCache = cachetools.TTLCache(
    maxsize=CACHE_MAXSIZE, ttl=CACHE_TTL_SECONDS
)
_lock = threading.Lock()


def cache_key(question: str) -> str:
    return hashlib.sha256(question.strip().lower().encode()).hexdigest()


def get(question: str) -> CachedAnswer | None:
    """Return the cached answer for a question, or None on a miss."""
    with _lock:
        return response_cache.get(cache_key(question))


def put(
    question: str,
    answer: str,
    chunks: list[Chunk] | None = None,
    query_type: str = "",
    extend_history: bool = True,
    patches: tuple[dict, ...] = (),
    audio: tuple[dict, ...] = (),
) -> None:
    with _lock:
        response_cache[cache_key(question)] = CachedAnswer(
            question=question,
            answer=answer,
            chunks=list(chunks or []),
            query_type=query_type,
            extend_history=extend_history,
            patches=patches,
            audio=audio,
        )


def clear() -> None:
    with _lock:
        response_cache.clear()


def stats() -> dict[str, int | float]:
    return {
        "size": len(response_cache),
        "maxsize": response_cache.maxsize,
        "ttl_seconds": response_cache.ttl,
    }
