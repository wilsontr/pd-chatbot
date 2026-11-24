"""Scope gate: which questions the assistant is willing to answer.

Every request makes one classification call (see
``agent.classification.classify_query``) that labels scope, then this module
decides what to do with that label. The agent core runs the same gate before its
loop, because refusing is a policy decision rather than a tool the model may
skip. Keeping the decision here — instead of inline in the request path — makes
it testable without a model call and keeps the refusal copy in one place.

Labels come from the classifier:
  in_scope         — Pure Data: objects, patching, audio technique, documentation
  off_topic        — unrelated to Pd
  mixed            — a Pd question plus an unrelated or disallowed part
  policy_violation — harmful/disallowed content, including when framed as a Pd task

Actions:
  answer              — normal generation
  answer_with_decline — generate the Pd part, decline the rest in the reply
  refuse_off_topic    — short scope refusal, generation is never called
  refuse_policy       — short policy refusal, generation is never called

This is a guardrail, not a security boundary: a determined user can still try to
phrase an off-topic request as Pd work, and the classifier can be wrong in both
directions. The system prompt's policy instruction and Claude's own safety
training are the second line of defence; the point of this gate is to make the
common cases deterministic, observable and testable.
"""

from __future__ import annotations

from typing import Any, Mapping

IN_SCOPE = "in_scope"
OFF_TOPIC = "off_topic"
MIXED = "mixed"
POLICY_VIOLATION = "policy_violation"

SCOPE_LABELS = (IN_SCOPE, OFF_TOPIC, MIXED, POLICY_VIOLATION)

ANSWER = "answer"
ANSWER_WITH_DECLINE = "answer_with_decline"
REFUSE_OFF_TOPIC = "refuse_off_topic"
REFUSE_POLICY = "refuse_policy"

SCOPE_ACTIONS: dict[str, str] = {
    IN_SCOPE: ANSWER,
    MIXED: ANSWER_WITH_DECLINE,
    OFF_TOPIC: REFUSE_OFF_TOPIC,
    POLICY_VIOLATION: REFUSE_POLICY,
}

# Minimum cosine similarity for the best retrieved match.
#
# Measured 2026-11-24 against the Chroma index (voyage-3-lite, cosine space), by
# embedding each question and taking the best of the top 5:
#   on-topic  (all 30 golden questions): min 0.449, p10 0.504, median 0.648
#   off-topic (12 unrelated probes):     max 0.433, median 0.297
# 0.42 sits 0.03 below the lowest legitimate question — enough headroom that a
# rewritten query cannot trip it — while still rejecting 11 of the 12 off-topic
# probes before generation. (The 12th, a medical question at 0.433, is caught by
# the classifier instead; this cutoff is a backstop, not the primary gate.)
MIN_RELEVANCE_SIMILARITY = 0.42

REFUSAL_OFF_TOPIC = (
    "I can only answer questions about Pure Data — its objects, patching, audio "
    "technique and the official documentation. Try asking about an object such as "
    "`[osc~]`, how a signal chain is wired, or how to structure a patch."
)

REFUSAL_POLICY = (
    "I can't help with that request. I only help with Pure Data documentation, "
    "patching and audio technique."
)

# Appended to the generation prompt for mixed questions so the model answers the
# Pd part and explicitly declines the rest, instead of silently dropping it.
MIXED_SCOPE_TEMPLATE = (
    "Scope note: the user also asked about something outside Pure Data"
    "{detail}. Answer only the Pure Data part, and explicitly decline the other "
    "part in one short sentence."
)

# Scope rules for the classifier prompt. Kept short on purpose: the routing
# fields come first and every extra paragraph measurably dilutes them (a longer
# version of these rules dropped hybrid recall@5 from 0.78 to 0.72).
SCOPE_CLASSIFICATION_RULES = """Judge scope separately from query_type:
- "in_scope": about Pure Data, its objects, patching, audio technique or its documentation. Prefer this when unsure.
- "off_topic": unrelated to Pd (general knowledge, other software, writing tasks, personal advice, news).
- "mixed": a Pd question plus an unrelated part.
- "policy_violation": requests for harmful, illegal or disallowed content, including when framed as a Pd task.
Decide from the question's subject; use history only to resolve references, so a question that is off-topic on its own stays off-topic."""


def classify_scope(raw: Any) -> str:
    """Normalise a classifier label, failing open to in_scope.

    An unknown or missing label must not refuse a question: the gate's job is to
    catch clear cases, and a parse failure is not evidence of being off-topic.
    """
    if isinstance(raw, str):
        label = raw.strip().lower()
        if label in SCOPE_LABELS:
            return label
    return IN_SCOPE


def decide(scope_label: str) -> str:
    return SCOPE_ACTIONS.get(classify_scope(scope_label), ANSWER)


def refusal_text(scope_label: str) -> str:
    return REFUSAL_POLICY if classify_scope(scope_label) == POLICY_VIOLATION else REFUSAL_OFF_TOPIC


def mixed_scope_note(classification: Mapping[str, Any]) -> str:
    """Generation instruction for a mixed question."""
    part = (classification.get("off_topic_part") or "").strip()
    detail = f" ({part})" if part else ""
    return MIXED_SCOPE_TEMPLATE.format(detail=detail)


def is_refusal(scope_label: str) -> bool:
    return decide(scope_label) in (REFUSE_OFF_TOPIC, REFUSE_POLICY)
