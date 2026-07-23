"""Generation evaluation: produce answers and score them via LLM-as-judge.

Supports:
  - Single-model evaluation (default Sonnet)
  - Model comparison (Sonnet vs Haiku vs any model, including other providers)
  - Subset sampling (evaluating every question burns tokens)

Cross-provider models: any model name starting with "deepseek" is routed to
DeepSeek's OpenAI-compatible API (requires DEEPSEEK_API_KEY) instead of Anthropic.
"""

import logging
import os
from typing import Any

import anthropic
import openai

from .dataset import GoldenDataset
from .judge import evaluate_generation, compare_models
from .retry import with_retries
from rag import (
    retrieve,
    _build_chat_messages,
    SYSTEM_PROMPT,
    child_chunks as _,
    parent_lookup as __,
)

logger = logging.getLogger(__name__)

DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# deepseek-v4-pro is a reasoning model — its hidden reasoning_content tokens count
# against the same completion budget as the visible answer, so a plain 4096 cap risks
# truncating the answer entirely (observed: one call spent 3298/4096 tokens on reasoning
# alone). Give it more headroom; other deepseek models keep the default.
_DEEPSEEK_MAX_TOKENS = {
    "deepseek-v4-pro": 16000,
}
_DEFAULT_MAX_TOKENS = 4096

_deepseek_client: openai.OpenAI | None = None

# $ per million tokens, pulled from published pricing (platform.claude.com/docs and
# api-docs.deepseek.com) at the time this was written — check current rates before
# relying on this for anything beyond relative comparison.
PRICING_PER_MTOK: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {"input": 3.00, "cache_read": 0.30, "output": 15.00},
    "claude-haiku-4-5": {"input": 1.00, "cache_read": 0.10, "output": 5.00},
    "deepseek-v4-flash": {"input": 0.14, "cache_read": 0.0028, "output": 0.28},
    "deepseek-v4-pro": {"input": 0.435, "cache_read": 0.003625, "output": 0.87},
}


def _is_deepseek_model(model: str) -> bool:
    return model.lower().startswith("deepseek")


def _max_tokens_for(model: str) -> int:
    return _DEEPSEEK_MAX_TOKENS.get(model, _DEFAULT_MAX_TOKENS)


def _cost_usd(model: str, usage: dict[str, int]) -> float | None:
    """Compute exact request cost from measured token usage. Returns None if the
    model isn't in PRICING_PER_MTOK (comparison-only models, e.g. a judge model
    run standalone, or a new model id not yet added to the table)."""
    rates = PRICING_PER_MTOK.get(model)
    if rates is None:
        return None
    uncached_input = usage.get("input_tokens", 0) - usage.get("cache_read_tokens", 0)
    return (
        uncached_input * rates["input"]
        + usage.get("cache_read_tokens", 0) * rates["cache_read"]
        + usage.get("output_tokens", 0) * rates["output"]
    ) / 1_000_000


def _get_deepseek_client() -> openai.OpenAI:
    """Lazily create the DeepSeek client so DEEPSEEK_API_KEY is only required
    when a deepseek-* model is actually being evaluated."""
    global _deepseek_client
    if _deepseek_client is None:
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError(
                "DEEPSEEK_API_KEY is not set — required to evaluate deepseek-* models"
            )
        _deepseek_client = openai.OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)
    return _deepseek_client


def _generate(
    client: anthropic.Anthropic,
    model: str,
    question: str,
    context_chunks: list[dict[str, Any]],
    history: list[dict[str, str]] | None = None,
) -> tuple[str, dict[str, int]]:
    """Generate a response with a specific model, routing to the right provider.

    `client` is the Anthropic client used for anthropic/claude-* models; deepseek-*
    models are routed to a separate OpenAI-compatible client (see _get_deepseek_client).

    Returns (answer, usage) where usage has input_tokens/output_tokens/cache_read_tokens,
    read directly off the API response so cost figures reflect real measured usage.
    """
    if history is None:
        history = []
    system_block, messages = _build_chat_messages(question, context_chunks, history)

    if _is_deepseek_model(model):
        # DeepSeek's chat-completions API takes a flat messages list with the system
        # prompt as a single "system" message — no content-block/cache_control support.
        system_text = "\n\n".join(block["text"] for block in system_block)
        deepseek = _get_deepseek_client()
        response = with_retries(
            deepseek.chat.completions.create,
            model=model,
            max_tokens=_max_tokens_for(model),
            messages=[{"role": "system", "content": system_text}] + messages,
        )
        u = response.usage
        usage = {
            "input_tokens": getattr(u, "prompt_tokens", 0) or 0,
            "output_tokens": getattr(u, "completion_tokens", 0) or 0,
            "cache_read_tokens": getattr(u, "prompt_cache_hit_tokens", 0) or 0,
        }
        return response.choices[0].message.content, usage

    response = with_retries(
        client.messages.create,
        model=model,
        max_tokens=4096,
        system=system_block,
        messages=messages,
    )
    u = response.usage
    usage = {
        "input_tokens": u.input_tokens,
        "output_tokens": u.output_tokens,
        "cache_read_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
    }
    return response.content[0].text, usage


def run_generation_eval(
    *,
    dataset: GoldenDataset,
    model: str = "claude-sonnet-4-6",
    judge_model: str = "claude-haiku-4-5",
    compare: list[str] | None = None,
    sample: int | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    """Run generation evaluation.

    Args:
        dataset: The golden dataset
        model: Primary generation model
        judge_model: Model used for LLM-as-judge scoring
        compare: Optional list of additional models for comparison
        sample: Limit to N entries (None = all)
        verbose: Print progress

    Returns:
        Dict with per-entry and aggregate scores.
    """
    entries = dataset.entries
    if sample:
        entries = entries[:sample]

    client = anthropic.Anthropic()
    models_to_run = [model]
    if compare:
        models_to_run.extend(m for m in compare if m != model)

    results: dict[str, list[dict[str, Any]]] = {m: [] for m in models_to_run}

    for i, entry in enumerate(entries):
        if verbose:
            print(f"[{i+1}/{len(entries)}] {entry.id}: {entry.question[:60]}...")

        chunks, classification = retrieve(entry.question, entry.history)
        if not chunks:
            if verbose:
                print(f"  SKIP — no chunks retrieved")
            continue

        model_answers: dict[str, str] = {}
        model_usage: dict[str, dict[str, int]] = {}
        for m in models_to_run:
            try:
                answer, usage = _generate(client, m, entry.question, chunks, entry.history)
                model_answers[m] = answer
                model_usage[m] = usage
                if verbose:
                    print(f"  [{m}] generated ({len(answer)} chars, "
                          f"{usage['input_tokens']}in/{usage['output_tokens']}out tokens)")
            except Exception as exc:
                logger.error("Generation failed for %s with %s: %s", entry.id, m, exc)
                model_answers[m] = f"[ERROR: {exc}]"
                model_usage[m] = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0}

        if len(model_answers) > 1:
            scores = compare_models(
                client,
                question=entry.question,
                answers=model_answers,
                context_chunks=chunks,
                judge_model=judge_model,
            )
            # Flatten: scores = {"sonnet": {"faithfulness": {...}, ...}, "haiku": {...}}
            for m_name, m_scores in scores.items():
                entry_result = {
                    "entry_id": entry.id,
                    "model": m_name,
                    "answer": model_answers[m_name],
                    "scores": m_scores,
                    "usage": model_usage[m_name],
                }
                results[m_name].append(entry_result)
                if verbose:
                    for dim, s in m_scores.items():
                        print(f"  [{m_name}] {dim}: {s.get('score')} — {s.get('explanation', '')[:80]}")
        else:
            m = models_to_run[0]
            scores = evaluate_generation(
                client,
                question=entry.question,
                answer=model_answers[m],
                context_chunks=chunks,
                judge_model=judge_model,
            )
            entry_result = {
                "entry_id": entry.id,
                "model": m,
                "answer": model_answers[m],
                "scores": scores,
                "usage": model_usage[m],
            }
            results[m].append(entry_result)
            if verbose:
                for dim, s in scores.items():
                    print(f"  {dim}: {s.get('score')} — {s.get('explanation', '')[:80]}")

    # Aggregate
    summary: dict[str, Any] = {"per_model": {}}
    for m_name, entries_list in results.items():
        model_summary: dict[str, Any] = {"n": len(entries_list)}
        for dim in ("faithfulness", "relevance", "citation_correctness"):
            scores = [
                e["scores"][dim]["score"]
                for e in entries_list
                if e["scores"][dim].get("score") is not None
            ]
            dropped = len(entries_list) - len(scores)
            # Track how many judge calls failed to yield a usable score for this
            # dimension. A silently-excluded score biases the mean — it's dropped
            # from the average rather than counted, and unparseable judge output
            # correlates with verbose explanations, which correlates with bad
            # (low-scoring) answers. So a nonzero count here means the mean above
            # may be inflated, not just "based on slightly fewer samples."
            if dropped:
                model_summary[f"{dim}_dropped"] = dropped
            if scores:
                model_summary[f"{dim}_mean"] = round(sum(scores) / len(scores), 2)
                model_summary[f"{dim}_min"] = min(scores)
                model_summary[f"{dim}_max"] = max(scores)

        usages = [e["usage"] for e in entries_list if e.get("usage")]
        if usages:
            avg_input = sum(u["input_tokens"] for u in usages) / len(usages)
            avg_output = sum(u["output_tokens"] for u in usages) / len(usages)
            avg_cache_read = sum(u["cache_read_tokens"] for u in usages) / len(usages)
            costs = [_cost_usd(m_name, u) for u in usages]
            costs = [c for c in costs if c is not None]
            model_summary["avg_input_tokens"] = round(avg_input, 1)
            model_summary["avg_output_tokens"] = round(avg_output, 1)
            model_summary["avg_cache_read_tokens"] = round(avg_cache_read, 1)
            if costs:
                avg_cost = sum(costs) / len(costs)
                model_summary["avg_cost_usd"] = round(avg_cost, 5)
                model_summary["cost_per_1000_queries_usd"] = round(avg_cost * 1000, 2)

        summary["per_model"][m_name] = model_summary

    summary["entries"] = [
        {
            "id": m["entry_id"],
            "model": m["model"],
            "scores": {
                dim: {"score": s.get("score"), "explanation": s.get("explanation")}
                for dim, s in m["scores"].items()
            },
            "usage": m.get("usage"),
        }
        for model_results in results.values()
        for m in model_results
    ]

    return summary
