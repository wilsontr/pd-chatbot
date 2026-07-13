"""LLM-as-judge prompts and scoring for generation evaluation.

Uses Claude to score generated answers on:
  - Faithfulness: does the answer stick to retrieved context?
  - Answer relevance: does it actually answer the question?
  - Citation correctness: are sources cited accurately?

Also supports model-comparison mode: generate with N models, compare scores.
"""

import json
import logging
import re
from typing import Any

import anthropic

logger = logging.getLogger(__name__)

JUDGE_SYSTEM_PROMPT = (
    "You are an expert evaluator of RAG (Retrieval-Augmented Generation) system outputs. "
    "Your job is to score answers against a strict rubric. Be objective and consistent. "
    "Return ONLY valid JSON — no preamble, no markdown fences."
)

FAITHFULNESS_PROMPT = """Score the faithfulness of the generated answer on a 1-5 scale.

Faithfulness means: does the answer make ONLY claims that are directly supported by
the provided context? Penalize hallucinations, fabrications, or unsupported statements.

**Context (retrieved documentation):**
{context}

**Question:**
{question}

**Generated Answer:**
{answer}

**Scoring rubric:**
5 — Every claim is directly supported by the context. No unsupported statements.
4 — One minor unsupported detail, but core claims are grounded.
3 — Multiple unsupported details OR one significant unsupported claim.
2 — Major portions of the answer are unsupported by the context.
1 — Nearly all claims are fabricated or contradict the context.

Return JSON: {{"score": <int 1-5>, "explanation": "<one sentence>"}}"""

RELEVANCE_PROMPT = """Score the answer relevance on a 1-5 scale.

Answer relevance means: does the response directly address the user's question?
Consider whether it answers the specific question asked, not a related but different question.

**Question:**
{question}

**Generated Answer:**
{answer}

**Scoring rubric:**
5 — Directly and completely answers the question. No irrelevant tangents.
4 — Answers the question well, with minor digressions.
3 — Partially answers the question or includes significant irrelevant content.
2 — Barely addresses the question; mostly off-topic.
1 — Does not answer the question at all; completely off-topic.

Return JSON: {{"score": <int 1-5>, "explanation": "<one sentence>"}}"""

CITATION_PROMPT = """Score citation correctness on a 1-5 scale.

Citation correctness means: are the sources/URLs cited accurately? Does the answer
reference the correct documentation sections? Are claims attributed to the right sources?

**Question:**
{question}

**Generated Answer:**
{answer}

**Available sources (the context that was provided):**
{context}

**Scoring rubric:**
5 — All citations are accurate and point to the correct sources.
4 — Mostly correct citations, one minor error.
3 — Some citations are incorrect or missing where they should exist.
2 — Most citations are wrong or fabricated.
1 — Citations are entirely fabricated or missing when required.

Return JSON: {{"score": <int 1-5>, "explanation": "<one sentence>"}}"""


def _score_single(
    client: anthropic.Anthropic,
    judge_model: str,
    prompt: str,
    context: str,
    question: str,
    answer: str,
) -> dict[str, Any]:
    """Run a single LLM-as-judge scoring call."""
    filled = prompt.format(context=context, question=question, answer=answer)
    response = client.messages.create(
        model=judge_model,
        max_tokens=200,
        system=JUDGE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": filled}],
    )
    raw = response.content[0].text.strip()
    # Strip markdown code fences if the judge wraps JSON in ```json ... ```
    raw = re.sub(r'^```(?:json)?\s*\n?', '', raw)
    raw = re.sub(r'\n?```\s*$', '', raw)
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Judge returned unparseable JSON: %r", raw[:200])
        # Try to extract just the JSON portion
        for line in raw.split("\n"):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    pass
        return {"score": None, "explanation": f"Parse error: {raw[:100]}"}


def evaluate_generation(
    client: anthropic.Anthropic,
    *,
    question: str,
    answer: str,
    context_chunks: list[dict[str, Any]],
    judge_model: str = "claude-haiku-4-5",
) -> dict[str, Any]:
    """Score a single generated answer on all three dimensions.

    Args:
        client: Anthropic client
        question: The original user question
        answer: The generated answer to evaluate
        context_chunks: The retrieved chunks that were provided as context
        judge_model: Model to use for judging (Haiku is cheaper/faster)

    Returns:
        Dict with faithfulness, relevance, citation scores and explanations.
    """
    context_str = "\n\n---\n\n".join(
        f"[{c.get('heading_path', '')}]\n{c.get('text', '')[:500]}"
        for c in context_chunks
    )

    metrics = {}
    for name, prompt in [
        ("faithfulness", FAITHFULNESS_PROMPT),
        ("relevance", RELEVANCE_PROMPT),
        ("citation_correctness", CITATION_PROMPT),
    ]:
        result = _score_single(
            client, judge_model, prompt,
            context=context_str, question=question, answer=answer,
        )
        metrics[name] = result

    return metrics


def compare_models(
    client: anthropic.Anthropic,
    *,
    question: str,
    answers: dict[str, str],  # {"model_name": "answer text"}
    context_chunks: list[dict[str, Any]],
    judge_model: str = "claude-haiku-4-5",
) -> dict[str, Any]:
    """Score multiple model answers on the same question for comparison.

    Returns scores per model across all three dimensions.
    """
    results = {}
    for model_name, answer in answers.items():
        results[model_name] = evaluate_generation(
            client,
            question=question,
            answer=answer,
            context_chunks=context_chunks,
            judge_model=judge_model,
        )
    return results
