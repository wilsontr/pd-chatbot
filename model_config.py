"""Single source of truth for the models the app and its evaluations use.

The answer model is named in four places in ``rag.py`` (sync generation, the
async streaming path, and two observability spans), plus the evaluation CLI
default and the CI gate. Keeping the id here means a model switch is one edit
and cannot drift between production and the gate that guards it.

``JUDGE_MODEL`` is the model that scores answers (CI gate, and the inline
faithfulness check in production). It is deliberately pinned to haiku-4-5: the
gate thresholds in ``evaluation/gates.py`` were measured with it, and changing
it invalidates that calibration until re-measured.

Token budgets live here too because thinking models spend output tokens on
reasoning before writing an answer. ``max_tokens_for`` gives those models room
so a starved budget cannot masquerade as a quality regression.
"""

from __future__ import annotations

from typing import Any

# The model that answers user questions (production and, via the eval defaults,
# the CI gate).
ANSWER_MODEL = "claude-haiku-5-5"

# The model that scores answers on the 1-5 rubric.
JUDGE_MODEL = "claude-haiku-4-5"

# Reasoning models: hidden reasoning tokens count against max_tokens, so a plain
# 4096 cap can be spent before the answer starts (observed: 3298/4096 tokens on
# reasoning alone for deepseek-v4-pro, and truncated Claude 5.5 answers).
DEEPSEEK_MAX_TOKENS: dict[str, int] = {
    "deepseek-v4-pro": 16000,
}
MAX_TOKENS_OVERRIDES: dict[str, int] = {
    "claude-sonnet-5-5": 16000,
    "claude-haiku-5-5": 16000,
}
DEFAULT_MAX_TOKENS = 4096


def max_tokens_for(model: str) -> int:
    """Output-token budget for a model, with headroom for thinking models."""
    return MAX_TOKENS_OVERRIDES.get(
        model, DEEPSEEK_MAX_TOKENS.get(model, DEFAULT_MAX_TOKENS)
    )


# Claude 5.x decides on its own whether to reason before answering. Measured on
# 2026-11-24 (30 entries, judge claude-haiku-4-5):
#   thinking on : faithfulness 4.92, citation 4.58, p50 answer 8.1 s, 4.7 s to first token
#   thinking off: faithfulness 4.63, citation 4.50, p50 answer 4.2 s, ~0.6 s to first token
# Thinking is disabled: the 0.08 citation cost (4.58 -> 4.50) is accepted in
# exchange for restoring a sub-second first token, which the streaming UI depends
# on. Everything still passes the CI gate (faithfulness 4.0, relevance 4.5,
# citation 4.0). Re-measured after the change: see docs/MODEL_COMPARISON.md.
DISABLE_THINKING = True

# Models that accept the thinking parameter (Claude 5.x family). Older models
# treat it as a no-op, but the parameter is only sent to these to avoid passing
# an unsupported argument to another provider.
THINKING_CAPABLE_MODELS = {"claude-haiku-5-5", "claude-sonnet-5-5"}


def thinking_kwargs(model: str) -> dict[str, Any]:
    """Extra request kwargs that keep thinking models off the first-token path."""
    if DISABLE_THINKING and model in THINKING_CAPABLE_MODELS:
        return {"thinking": {"type": "disabled"}}
    return {}
