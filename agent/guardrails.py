"""Guardrails: prompt-injection detection and groundedness gating.

Lightweight heuristics, not a security boundary — the system prompt already
constrains the model to prefer documentation. These checks detect the most common
attack patterns and flag ungrounded responses for observability, and they run
identically in the pipeline and in the agent core.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .llm import sync_client
from .observability import cost_from_usage, record_model_call, start_obs
from .types import Chunk
from evaluation.judge import score_faithfulness
from model_config import JUDGE_MODEL

logger = logging.getLogger(__name__)

_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above|the)\s+(instructions?|prompts?|rules?|directives?)", re.IGNORECASE),
    re.compile(r"you\s+are\s+(now\s+|no\s+longer\s+)?(a\s+|an\s+)?(?!.*?\bhelpful\b).{0,20}(assistant|chatbot|helper|ai)", re.IGNORECASE),
    re.compile(r"(override|disregard|forget)\s+(your\s+)?(instructions?|prompts?|rules?|system\s+prompt|guidelines?)", re.IGNORECASE),
    re.compile(r"system\s+(prompt|message|instruction)", re.IGNORECASE),
    re.compile(r"(DAN|jailbreak|roleplay|developer\s*mode)", re.IGNORECASE),
    re.compile(r"from\s+now\s+on\s+you\s+(are|will|must|should)", re.IGNORECASE),
    re.compile(r"(do\s+not|don't|never)\s+(follow|obey|use|reference)\s+(your\s+)?(instructions?|prompts?|rules?)", re.IGNORECASE),
]


def check_prompt_injection(question: str) -> bool:
    """Return True if the question contains known prompt-injection patterns."""
    return any(p.search(question) for p in _INJECTION_PATTERNS)


# Faithfulness score (1-5, LLM-as-judge) at or below this is treated as a real hallucination
# risk rather than noise — see evaluation/judge.py's rubric ("major/near-total unsupported claims").
FAITHFULNESS_RETRY_THRESHOLD = 2

FAITHFULNESS_RETRY_PROMPT = """Your previous answer contained claims that a faithfulness \
check flagged as not supported by the provided documentation excerpts \
(reason: {explanation}).

Revise your answer:
- Remove or qualify any claim not directly supported by the excerpts.
- If you're not sure something is covered by the docs, say so explicitly \
("not covered in the documentation, but...") instead of stating it as fact.
- Keep the same formatting rules (pd-patch blocks, code spans, tables) as before.

The documentation excerpts and question are unchanged from before this correction."""

# Appended to a streamed answer when the guard cannot retry: the answer is
# already on the wire, so the only honest action is a visible disclaimer.
LOW_FAITHFULNESS_DISCLAIMER = (
    "\n\n_Note: parts of this answer may not be fully supported by the "
    "documentation excerpts — verify against the source docs above._"
)


def check_faithfulness(
    question: str, answer: str, chunks: list[Chunk]
) -> dict[str, Any] | None:
    """Run the LLM-as-judge faithfulness check inline (single Haiku call).

    Returns the judge's {"score": int, "explanation": str} dict, or None if there was
    nothing to check (no retrieved chunks) or the judge call itself failed — callers
    should treat None as "skip", not as a failure.
    """
    if not chunks:
        return None
    usage: dict[str, dict[str, int]] = {}
    obs = start_obs("faithfulness_guard", as_type="generation", model=JUDGE_MODEL)
    try:
        result = score_faithfulness(
            sync_client(), question=question, answer=answer, context_chunks=chunks,
            usage_sink=usage,
        )
        for model, tokens in usage.items():
            record_model_call("faithfulness_guard", model, tokens)
            obs.update(
                usage_details={
                    "input": tokens.get("input_tokens", 0),
                    "output": tokens.get("output_tokens", 0),
                    "cache_read": tokens.get("cache_read_tokens", 0),
                },
                cost_details=cost_from_usage(model, tokens),
            )
        return result
    except Exception:
        logger.warning("faithfulness judge call failed", exc_info=True)
    finally:
        obs.end()
