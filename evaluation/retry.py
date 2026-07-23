"""Retry helper for transient provider API errors.

Long eval runs make dozens to hundreds of API calls across three different
providers; a single transient overload/rate-limit blip anywhere shouldn't crash
a run that's already 20 minutes in. This wraps a call with bounded exponential
backoff, retrying only on errors that are actually transient.
"""

import logging
import random
import time
from typing import Any, Callable, TypeVar

import anthropic
import openai
from google.genai import errors as genai_errors

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Status codes that represent transient, retry-worthy failures across providers:
# 429 (rate limit), 500/502/503 (server-side hiccup), 529 (Anthropic-specific overload).
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 529}


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (anthropic.APIStatusError, openai.APIStatusError)):
        return exc.status_code in _RETRYABLE_STATUS_CODES
    if isinstance(exc, genai_errors.APIError):
        return getattr(exc, "code", None) in _RETRYABLE_STATUS_CODES
    return False


def with_retries(
    fn: Callable[..., T],
    *args: Any,
    max_attempts: int = 4,
    base_delay: float = 2.0,
    **kwargs: Any,
) -> T:
    """Call fn(*args, **kwargs), retrying on transient provider errors.

    Backs off exponentially (base_delay * 2**attempt, plus jitter) between
    attempts. Re-raises immediately on non-retryable errors, and re-raises the
    last error once max_attempts is exhausted.
    """
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if not _is_retryable(exc):
                raise
            last_exc = exc
            if attempt == max_attempts - 1:
                break
            delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
            logger.warning(
                "Transient API error (attempt %d/%d), retrying in %.1fs: %s",
                attempt + 1, max_attempts, delay, exc,
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc
