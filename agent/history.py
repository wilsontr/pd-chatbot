"""Conversation history compression.

The client sends its own history, so it is capped twice: old turns are summarised
into one snippet by a cheap model, and the returned history is truncated to
``HISTORY_MAX_OUTGOING`` items so the next request still fits the API's Pydantic
``max_length=20``. Shared by both the pipeline and the agent core.
"""

from __future__ import annotations

import asyncio
import logging

import newrelic.agent

from .llm import sync_client
from .observability import cost_details, record_model_call, start_obs_ctx, token_usage
from .types import HistoryItem
from evaluation.retry import with_retries
from llm_response import text_of

logger = logging.getLogger(__name__)

HISTORY_MODEL = "claude-haiku-4-5"

HISTORY_COMPRESS_AFTER = 8   # items (4 turns); older turns are summarized
HISTORY_MAX_OUTGOING = 18    # cap returned history so the next request passes Pydantic max_length=20


def compress_history(history: list[HistoryItem]) -> list[HistoryItem]:
    """Summarize turns older than HISTORY_COMPRESS_AFTER items using Haiku."""
    if len(history) <= HISTORY_COMPRESS_AFTER:
        return history

    recent = history[-HISTORY_COMPRESS_AFTER:]
    old = history[:-HISTORY_COMPRESS_AFTER]

    old_text = "\n".join(
        f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content'][:400]}"
        for m in old
    )
    obs = start_obs_ctx("compress_history", as_type="generation",
                        model=HISTORY_MODEL, input=old_text[:500])
    with obs as gen:
        with newrelic.agent.ExternalTrace('anthropic', 'api.anthropic.com', 'POST'):
            resp = with_retries(
                sync_client().messages.create,
                model=HISTORY_MODEL,
                max_tokens=200,
                messages=[{
                    "role": "user",
                    "content": (
                        "Summarize this Pure Data chatbot conversation in 2-3 sentences, "
                        "capturing key topics and important context:\n\n" + old_text
                    )
                }]
            )
        gen.update(output=text_of(resp), usage_details=token_usage(resp))
        if token_usage(resp):
            gen.update(cost_details=cost_details(HISTORY_MODEL, resp))
        record_model_call("history_compression", HISTORY_MODEL, token_usage(resp))
    summary = text_of(resp).strip()
    logger.info("compressed %d history items into summary", len(old))

    return [
        {"role": "user", "content": f"[Conversation summary: {summary}]"},
        {"role": "assistant", "content": "Understood."},
    ] + recent


async def compress_history_async(history: list[HistoryItem]) -> list[HistoryItem]:
    """``compress_history`` off the event loop (it makes a blocking API call)."""
    return await asyncio.to_thread(compress_history, history)
