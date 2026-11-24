"""Generation evaluation: produce answers and score them via LLM-as-judge.

Supports:
  - Single-model evaluation (default is model_config.ANSWER_MODEL)
  - Model comparison (Sonnet vs Haiku vs any model, including other providers)
  - Subset sampling (evaluating every question burns tokens)

Cross-provider models: any model name starting with "deepseek" is routed to
DeepSeek's OpenAI-compatible API (requires DEEPSEEK_API_KEY) instead of Anthropic.
"""

import logging
import os
import statistics
import time
from typing import Any

import anthropic
import openai

from llm_response import text_of
from model_config import (
    ANSWER_MODEL,
    JUDGE_MODEL,
    max_tokens_for as _max_tokens_for,
    thinking_kwargs,
)

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

_deepseek_client: openai.OpenAI | None = None

from pricing import PRICING_PER_MTOK, cache_savings_usd, cost_usd as _cost_usd


def _is_deepseek_model(model: str) -> bool:
    return model.lower().startswith("deepseek")


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
    system_prompt: str | None = None,
) -> tuple[str, dict[str, int]]:
    """Generate a response with a specific model, routing to the right provider.

    `client` is the Anthropic client used for anthropic/claude-* models; deepseek-*
    models are routed to a separate OpenAI-compatible client (see _get_deepseek_client).

    Returns (answer, usage) where usage has input_tokens/output_tokens/cache_read_tokens,
    read directly off the API response so cost figures reflect real measured usage.
    """
    if history is None:
        history = []
    system_block, messages = _build_chat_messages(
        question, context_chunks, history, system_prompt=system_prompt
    )

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
        max_tokens=_max_tokens_for(model),
        system=system_block,
        messages=messages,
        **thinking_kwargs(model),
    )
    u = response.usage
    usage = {
        "input_tokens": u.input_tokens,
        "output_tokens": u.output_tokens,
        "cache_read_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
    }
    return text_of(response), usage


def run_generation_eval(
    *,
    dataset: GoldenDataset,
    model: str = ANSWER_MODEL,
    judge_model: str = JUDGE_MODEL,
    compare: list[str] | None = None,
    sample: int | None = None,
    system_prompt: str | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    """Run generation evaluation.

    Args:
        dataset: The golden dataset
        model: Primary generation model
        judge_model: Model used for LLM-as-judge scoring
        compare: Optional list of additional models for comparison
        sample: Limit to N entries (None = all)
        system_prompt: Alternative system prompt to score instead of the shipped
            one (prompt-variant experiments); None uses rag.SYSTEM_PROMPT
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
    # Judge-token accumulator per generation model: {model: {judge_model: counts}}.
    # Measured, not estimated, so the CI job summary can state what a run cost.
    judge_usage: dict[str, dict[str, dict[str, int]]] = {m: {} for m in models_to_run}

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
        model_latency_ms: dict[str, float] = {}
        for m in models_to_run:
            try:
                started = time.perf_counter()
                answer, usage = _generate(
                    client, m, entry.question, chunks, entry.history,
                    system_prompt=system_prompt,
                )
                latency_ms = (time.perf_counter() - started) * 1000
                model_answers[m] = answer
                model_usage[m] = usage
                model_latency_ms[m] = latency_ms
                if verbose:
                    print(f"  [{m}] generated ({len(answer)} chars, "
                          f"{usage['input_tokens']}in/{usage['output_tokens']}out tokens)")
            except Exception as exc:
                logger.error("Generation failed for %s with %s: %s", entry.id, m, exc)
                model_answers[m] = f"[ERROR: {exc}]"
                model_usage[m] = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0}
                model_latency_ms[m] = 0.0

        if len(model_answers) > 1:
            scores = compare_models(
                client,
                question=entry.question,
                answers=model_answers,
                context_chunks=chunks,
                judge_model=judge_model,
                usage_sinks=judge_usage,
            )
            # Flatten: scores = {"sonnet": {"faithfulness": {...}, ...}, "haiku": {...}}
            for m_name, m_scores in scores.items():
                entry_result = {
                    "entry_id": entry.id,
                    "model": m_name,
                    "answer": model_answers[m_name],
                    "scores": m_scores,
                    "usage": model_usage[m_name],
                    "latency_ms": round(model_latency_ms.get(m_name, 0.0), 1),
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
                usage_sink=judge_usage[m],
            )
            entry_result = {
                "entry_id": entry.id,
                "model": m,
                "answer": model_answers[m],
                "scores": scores,
                "usage": model_usage[m],
                "latency_ms": round(model_latency_ms.get(m, 0.0), 1),
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
        judge_usages = judge_usage.get(m_name, {})
        judge_costs = [
            _cost_usd(judge_model_name, u) for judge_model_name, u in judge_usages.items()
        ]
        judge_costs = [c for c in judge_costs if c is not None]

        if judge_usages:
            model_summary["judge_input_tokens"] = sum(
                u["input_tokens"] for u in judge_usages.values()
            )
            model_summary["judge_output_tokens"] = sum(
                u["output_tokens"] for u in judge_usages.values()
            )
        if judge_costs:
            judge_cost = sum(judge_costs)
            model_summary["judge_cost_usd"] = round(judge_cost, 5)
            if entries_list:
                model_summary["avg_judge_cost_usd"] = round(
                    judge_cost / len(entries_list), 5
                )

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
                judge_total = sum(judge_costs)
                model_summary["run_total_cost_usd"] = round(
                    sum(costs) + judge_total, 5
                )
                if entries_list:
                    model_summary["avg_total_cost_usd"] = round(
                        (sum(costs) + judge_total) / len(entries_list), 5
                    )

        latencies = [
            e["latency_ms"] for e in entries_list if e.get("latency_ms") is not None
        ]
        if latencies:
            ordered = sorted(latencies)
            # Nearest-rank p95: the value at the 95th percentile index.
            p95_index = max(0, min(len(ordered) - 1, int(round(0.95 * len(ordered))) - 1))
            model_summary["avg_latency_ms"] = round(sum(ordered) / len(ordered), 1)
            model_summary["p50_latency_ms"] = round(statistics.median(ordered), 1)
            model_summary["p95_latency_ms"] = round(ordered[p95_index], 1)
            model_summary["max_latency_ms"] = round(max(ordered), 1)

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
            "latency_ms": m.get("latency_ms"),
        }
        for model_results in results.values()
        for m in model_results
    ]

    return summary
