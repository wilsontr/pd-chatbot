"""Query classification: routing, object names, scope, query rewriting.

One cheap Haiku call per question produces everything the request needs to route
itself: a standalone rewritten query, the corpus to search, the Pd objects named,
and the scope label the gate acts on. Both the pipeline and the agent core use it
— in the agent's case the gate still runs before the loop, because refusing
off-topic questions is a policy decision, not a tool the model should be able to
skip.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import newrelic.agent

import scope
from .llm import sync_client
from .observability import cost_details, record_model_call, start_obs_ctx, token_usage
from .prompt import CLASSIFIER_SYSTEM_PROMPT
from .types import HistoryItem
from evaluation.retry import with_retries
from llm_response import text_of

logger = logging.getLogger(__name__)

CLASSIFIER_MODEL = "claude-haiku-4-5"


@newrelic.agent.function_trace()
def classify_query(question: str, history: list[HistoryItem]) -> dict[str, Any]:
    """
    Classify the query type and extract object names.
    Also rewrites the question as a standalone search query
    if there is conversation history that provides context.
    """
    history_text = ""
    if history:
        recent = history[-4:]
        history_text = "\n".join([
            f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content'][:200]}"
            for m in recent
        ])

    # Scope rules go in the system block so the routing instructions below stay
    # almost word-for-word what they were before the gate existed: appending scope
    # rules to the same message measurably degraded retrieval (hybrid recall@5
    # 0.78 -> 0.75) by changing how the model rewrote queries.
    prompt = f"""You are helping route questions about Pure Data (Pd) to the right documentation source.

{"Conversation history:\n" + history_text if history_text else ""}

Question: {question}

Respond in JSON with these fields:
- "rewritten_query": standalone version of the question incorporating any context from history
- "query_type": one of "object_reference", "conceptual", or "both". Use "object_reference" when the question is primarily about one or more named Pd objects (for example "What are the inlets of [osc~]?"); use "conceptual" for patching or technique questions that name no object; use "both" only when the question genuinely needs both kinds of documentation.
- "object_names": list of Pd object names mentioned (e.g. ["osc~", "pack"]), empty list if none
- "scope": one of "in_scope", "off_topic", "mixed", "policy_violation"
- "off_topic_part": the non-Pd part when scope is "mixed", otherwise ""
- "pd_query": when scope is "mixed", the Pure Data part of the question as a standalone search query; otherwise the same as "rewritten_query"

Return only the JSON object, no other text."""

    obs_cm = start_obs_ctx("classify_query", as_type="generation",
                           model=CLASSIFIER_MODEL, input=question)
    with obs_cm as obs:
        with newrelic.agent.ExternalTrace('anthropic', 'api.anthropic.com', 'POST'):
            response = with_retries(
                sync_client().messages.create,
                model=CLASSIFIER_MODEL,
                # Six fields plus scope reasoning; 200 tokens truncated the JSON
                # often enough to fall back to the default classification, which
                # measurably hurt hybrid retrieval (recall@5 0.78 -> 0.72).
                max_tokens=400,
                system=[{"type": "text", "text": CLASSIFIER_SYSTEM_PROMPT}],
                messages=[{"role": "user", "content": prompt}]
            )
        raw = text_of(response).strip()
        raw = re.sub(r'^```(?:json)?\s*\n', '', raw)
        raw = re.sub(r'\n```\s*$', '', raw)
        raw = raw.strip()
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("classify_query returned unparseable JSON: %r", raw)
            result = {
                "rewritten_query": question,
                "query_type": "both",
                "object_names": [],
                "scope": scope.IN_SCOPE,
                "off_topic_part": "",
                "pd_query": question,
            }
        result["scope"] = scope.classify_scope(result.get("scope"))
        result.setdefault("off_topic_part", "")
        result.setdefault("pd_query", result.get("rewritten_query", question))
        obs.update(output=result, usage_details=token_usage(response))
        if token_usage(response):
            obs.update(cost_details=cost_details(CLASSIFIER_MODEL, response))
        record_model_call("classify", CLASSIFIER_MODEL, token_usage(response))
    return result
