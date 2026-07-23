"""LLM-as-judge prompts and scoring for generation evaluation.

Uses an LLM to score generated answers on:
  - Faithfulness: does the answer stick to retrieved context?
  - Answer relevance: does it actually answer the question?
  - Citation correctness: are sources cited accurately?

Also supports model-comparison mode: generate with N models, compare scores.

Judge model: any model name starting with "gemini" routes to Google's Gemini API
(requires GEMINI_API_KEY) instead of Anthropic. Using a judge outside the vendor
families under comparison (Anthropic, DeepSeek) avoids the judge favoring its own
family's answers — see the model-comparison CLI usage for how to select it.
"""

import json
import logging
import os
import re
from typing import Any

import anthropic

from .retry import with_retries

logger = logging.getLogger(__name__)

_gemini_client: Any | None = None


def _is_gemini_model(model: str) -> bool:
    return model.lower().startswith("gemini")


def _get_gemini_client() -> Any:
    """Lazily create the Gemini client so GEMINI_API_KEY is only required
    when a gemini-* model is actually used as judge."""
    global _gemini_client
    if _gemini_client is None:
        from google import genai
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set — required to use a gemini-* judge model"
            )
        _gemini_client = genai.Client(api_key=api_key)
    return _gemini_client

JUDGE_SYSTEM_PROMPT = (
    "You are an expert evaluator of RAG (Retrieval-Augmented Generation) system outputs. "
    "Your job is to score answers against a strict rubric. Be objective and consistent. "
    "Return ONLY valid JSON — no preamble, no markdown fences."
)

FAITHFULNESS_PROMPT = """Score the faithfulness of the generated answer on a 1-5 scale.

Faithfulness means: does the answer avoid contradicting or fabricating information about
the documented behavior of Pure Data objects/concepts? Penalize hallucinations and
fabrications — statements that assert specific behavior, parameters, or facts that
contradict the context, or invent specifics (numeric defaults, inlet/outlet counts,
behavior) with no basis in the context.

Do NOT penalize:
- A synthesized example patch/signal-chain (e.g. a pd-patch diagram) that correctly
  combines objects whose individual behavior IS supported by the context — building an
  illustrative example is a deliberate, requested feature of this assistant, not a
  fabrication, as long as the underlying object behavior it relies on is accurate.
- General Pd knowledge that the answer explicitly flags as going beyond the documentation
  (e.g. "this isn't covered in the docs, but...") — flagging it correctly is the desired
  behavior, not a faithfulness violation, unless the flagged claim is itself factually wrong.
Only penalize these if they assert something false or inconsistent with the context, not
merely for being additional detail beyond it.

**Context (retrieved documentation):**
{context}

**Question:**
{question}

**Generated Answer:**
{answer}

**Scoring rubric:**
5 — No fabricated or contradictory claims. Any examples/elaborations are either grounded
    or explicitly flagged as beyond the docs, and are factually consistent with the context.
4 — One minor unflagged claim beyond the context, but nothing that contradicts it and no
    invented specifics (numbers, counts, behavior).
3 — Multiple unflagged claims beyond the context OR one invented specific (a number,
    parameter, or behavior detail with no basis in context or general Pd correctness).
2 — Major portions of the answer assert specifics that contradict the context or are
    fabricated with no grounding.
1 — Nearly all substantive claims are fabricated or contradict the context.

Return JSON on a single line: {{"score": <int 1-5>, "explanation": "<one concise sentence, max 20 words>"}}"""

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

Return JSON on a single line: {{"score": <int 1-5>, "explanation": "<one concise sentence, max 20 words>"}}"""

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

Return JSON on a single line: {{"score": <int 1-5>, "explanation": "<one concise sentence, max 20 words>"}}"""


def _score_single(
    client: anthropic.Anthropic,
    judge_model: str,
    prompt: str,
    context: str,
    question: str,
    answer: str,
) -> dict[str, Any]:
    """Run a single LLM-as-judge scoring call.

    `client` is the Anthropic client, used for claude-* judge models; gemini-*
    judge models are routed to a separate Gemini client (see _get_gemini_client)
    and `client` is unused in that branch.
    """
    filled = prompt.format(context=context, question=question, answer=answer)

    if _is_gemini_model(judge_model):
        from google.genai import types
        gclient = _get_gemini_client()
        response = with_retries(
            gclient.models.generate_content,
            model=judge_model,
            contents=filled,
            config=types.GenerateContentConfig(
                system_instruction=JUDGE_SYSTEM_PROMPT,
                # gemini-3.1-pro is a thinking model — hidden thought tokens count
                # against max_output_tokens same as deepseek-v4-pro's reasoning_content
                # (observed: 340/400 tokens spent on thoughts, truncating the visible
                # JSON answer almost every call). Budget generously for both.
                max_output_tokens=2000,
            ),
        )
        raw = (response.text or "").strip()
    else:
        response = with_retries(
            client.messages.create,
            model=judge_model,
            max_tokens=400,
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
        logger.warning("Judge returned unparseable JSON: %r", raw[:300])
        # Try to extract just the JSON portion (handles single-line JSON)
        for line in raw.split("\n"):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    pass
        # Last resort: the JSON is likely truncated (long explanation ran past
        # max_tokens) or has an unescaped character mid-string. The score itself
        # is almost always emitted before the explanation, so pull it out with a
        # regex rather than discarding a perfectly good score.
        score_match = re.search(r'"score"\s*:\s*(\d+)', raw)
        if score_match:
            return {
                "score": int(score_match.group(1)),
                "explanation": f"[truncated/malformed judge output] {raw[:150]}",
            }
        return {"score": None, "explanation": f"Parse error: {raw[:150]}"}


def score_faithfulness(
    client: anthropic.Anthropic,
    *,
    question: str,
    answer: str,
    context_chunks: list[dict[str, Any]],
    judge_model: str = "claude-haiku-4-5",
) -> dict[str, Any]:
    """Score only faithfulness (single Haiku call) — cheap enough to run inline at request time."""
    context_str = "\n\n---\n\n".join(
        f"[{c.get('heading_path', '')}]\n{c.get('text', '')}"
        for c in context_chunks
    )
    return _score_single(
        client, judge_model, FAITHFULNESS_PROMPT,
        context=context_str, question=question, answer=answer,
    )


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
        f"[{c.get('heading_path', '')}]\n{c.get('text', '')}"
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
