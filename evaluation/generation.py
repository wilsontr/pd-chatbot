"""Generation evaluation: produce answers and score them via LLM-as-judge.

Two arms, selected with ``mode``:

- ``pipeline`` — the fixed pipeline: retrieve once, then generate (``rag.py``).
- ``agent`` — the tool-use loop: the model calls ``search_docs`` itself, and is
  judged against the sections its own tool calls retrieved.
- ``both`` — run every model through both arms in the same pass, so the report
  can compare them on the same entries, judge and thresholds.

Supports:
  - Single-model evaluation (default is model_config.ANSWER_MODEL)
  - Model comparison (Sonnet vs Haiku vs any model, including other providers)
  - Subset sampling (evaluating every question burns tokens)

Cross-provider models: any model name starting with "deepseek" is routed to
DeepSeek's OpenAI-compatible API (requires DEEPSEEK_API_KEY) instead of Anthropic.
"""

import asyncio
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

from . import claims
from .dataset import GoldenDataset, GoldenEntry
from .judge import evaluate_generation
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

EVAL_MODES = ("pipeline", "agent", "both")

_AGENT_LABEL_SUFFIX = " (agent)"

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


def _agent_label(model: str) -> str:
    return f"{model}{_AGENT_LABEL_SUFFIX}"


def _agent_usage(usage: dict[str, int]) -> dict[str, int]:
    """Normalise the loop's usage dict to the harness's token spelling."""
    return {
        "input_tokens": int(usage.get("input", 0)),
        "output_tokens": int(usage.get("output", 0)),
        "cache_read_tokens": int(usage.get("cache_read", 0)),
    }


def _agent_answer(
    model: str,
    entry: GoldenEntry,
    system_prompt: str | None = None,
):
    """Run one golden entry through the agent loop (blocking)."""
    from agent.chat import answer_question
    from agent.loop import AgentConfig
    from agent.prompt import AGENT_SYSTEM_PROMPT

    config = AgentConfig(
        model=model,
        system_prompt=system_prompt or AGENT_SYSTEM_PROMPT,
    )
    return asyncio.run(
        answer_question(entry.question, entry.history, config=config)
    )


def _arm_names(mode: str) -> tuple[str, ...]:
    if mode == "both":
        return ("pipeline", "agent")
    if mode == "agent":
        return ("agent",)
    return ("pipeline",)


def run_generation_eval(
    *,
    dataset: GoldenDataset,
    model: str = ANSWER_MODEL,
    judge_model: str = JUDGE_MODEL,
    compare: list[str] | None = None,
    sample: int | None = None,
    system_prompt: str | None = None,
    verbose: bool = False,
    mode: str = "pipeline",
) -> dict[str, Any]:
    """Run generation evaluation.

    Args:
        dataset: The golden dataset
        model: Primary generation model
        judge_model: Model used for LLM-as-judge scoring
        compare: Optional list of additional models for comparison
        sample: Limit to N entries (None = all)
        system_prompt: Alternative system prompt to score instead of the shipped
            one (prompt-variant experiments); None uses the shipped prompt for
            both arms (``rag.SYSTEM_PROMPT`` / ``agent.prompt.AGENT_SYSTEM_PROMPT``)
        verbose: Print progress
        mode: "pipeline" (default, the CI gate), "agent", or "both"

    Returns:
        Dict with per-arm and per-entry scores. Arms are keyed by model name for
        the pipeline and "<model> (agent)" for the agent, so both can live in one
        report without colliding.
    """
    if mode not in EVAL_MODES:
        raise ValueError(f"mode must be one of {EVAL_MODES}, got {mode!r}")

    entries = dataset.entries
    if sample:
        entries = entries[:sample]

    client = anthropic.Anthropic()
    models_to_run = [model]
    if compare:
        models_to_run.extend(m for m in compare if m != model)

    arms = _arm_names(mode)
    labels: dict[tuple[str, str], str] = {
        (arm, m): (m if arm == "pipeline" else _agent_label(m))
        for arm in arms
        for m in models_to_run
    }

    results: dict[str, list[dict[str, Any]]] = {label: [] for label in labels.values()}
    # Judge-token accumulator per arm: {label: {judge_model: counts}}.
    # Measured, not estimated, so the CI job summary can state what a run cost.
    judge_usage: dict[str, dict[str, dict[str, int]]] = {
        label: {} for label in labels.values()
    }
    skipped: dict[str, int] = {label: 0 for label in labels.values()}

    def record(
        entry: GoldenEntry,
        arm: str,
        m: str,
        scores: dict[str, Any],
        answer: str,
        usage: dict[str, int],
        latency_ms: float,
        chunks: list[dict[str, Any]] | None = None,
        **extra: Any,
    ) -> None:
        label = labels[(arm, m)]
        # Deterministic checks travel with the answer, not the judge: an ordering
        # claim or an invented URL is wrong whether or not the judge notices, and
        # the judge has scored a self-contradicting answer 5/5/5 (see claims.py).
        violations = [
            v.as_dict()
            for v in claims.check_answer(entry.id, answer, chunks or [])
        ]
        results[label].append({
            "entry_id": entry.id,
            "model": m,
            "arm": arm,
            "label": label,
            "answer": answer,
            "scores": scores,
            "usage": usage,
            "latency_ms": round(latency_ms, 1),
            "claim_violations": violations,
            **extra,
        })
        if verbose:
            for dim, s in scores.items():
                print(f"  [{label}] {dim}: {s.get('score')} — {s.get('explanation', '')[:80]}")

    for i, entry in enumerate(entries):
        if verbose:
            print(f"[{i+1}/{len(entries)}] {entry.id}: {entry.question[:60]}...")

        pipeline_chunks: list[dict[str, Any]] | None = None
        if "pipeline" in arms:
            pipeline_chunks, _classification = retrieve(entry.question, entry.history)

        for arm in arms:
            if arm == "pipeline":
                chunks = pipeline_chunks or []
                if not chunks:
                    if verbose:
                        print("  SKIP — no chunks retrieved")
                    for m in models_to_run:
                        skipped[labels[(arm, m)]] += 1
                    continue
                for m in models_to_run:
                    label = labels[(arm, m)]
                    try:
                        started = time.perf_counter()
                        answer, usage = _generate(
                            client, m, entry.question, chunks, entry.history,
                            system_prompt=system_prompt,
                        )
                        latency_ms = (time.perf_counter() - started) * 1000
                        if verbose:
                            print(f"  [{label}] generated ({len(answer)} chars, "
                                  f"{usage['input_tokens']}in/{usage['output_tokens']}out tokens)")
                    except Exception as exc:
                        logger.error("Generation failed for %s with %s: %s", entry.id, m, exc)
                        answer = f"[ERROR: {exc}]"
                        usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0}
                        latency_ms = 0.0
                    scores = evaluate_generation(
                        client,
                        question=entry.question,
                        answer=answer,
                        context_chunks=chunks,
                        judge_model=judge_model,
                        usage_sink=judge_usage[label],
                    )
                    record(entry, arm, m, scores, answer, usage, latency_ms,
                           chunks=chunks, chunks_retrieved=len(chunks))
            else:
                for m in models_to_run:
                    label = labels[(arm, m)]
                    try:
                        started = time.perf_counter()
                        result = _agent_answer(m, entry, system_prompt=system_prompt)
                        latency_ms = (time.perf_counter() - started) * 1000
                        answer = result.answer
                        chunks = list(result.chunks)
                        usage = _agent_usage(result.usage)
                        if verbose:
                            print(f"  [{label}] generated ({len(answer)} chars, "
                                  f"{len(chunks)} chunks, {result.tool_calls} tool calls)")
                    except Exception as exc:
                        logger.error("Agent run failed for %s with %s: %s", entry.id, m, exc)
                        continue
                    if not chunks:
                        if verbose:
                            print("  SKIP — the agent retrieved no chunks")
                        skipped[label] += 1
                        continue
                    scores = evaluate_generation(
                        client,
                        question=entry.question,
                        answer=answer,
                        context_chunks=chunks,
                        judge_model=judge_model,
                        usage_sink=judge_usage[label],
                    )
                    record(entry, arm, m, scores, answer, usage, latency_ms,
                           chunks=chunks,
                           chunks_retrieved=len(chunks),
                           tool_calls=result.tool_calls,
                           retrieval_calls=result.retrieval_calls,
                           turns=result.turns)

    # Aggregate
    summary: dict[str, Any] = {"per_model": {}, "mode": mode}
    for label, entries_list in results.items():
        arm = "agent" if label.endswith(_AGENT_LABEL_SUFFIX) else "pipeline"
        model_summary: dict[str, Any] = {"n": len(entries_list), "mode": arm}
        if skipped.get(label):
            model_summary["skipped_no_context"] = skipped[label]
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

        if arm == "agent" and entries_list:
            model_summary["avg_tool_calls"] = round(
                sum(e.get("tool_calls", 0) for e in entries_list) / len(entries_list), 2
            )
            model_summary["avg_retrieval_calls"] = round(
                sum(e.get("retrieval_calls", 0) for e in entries_list) / len(entries_list), 2
            )
            model_summary["avg_turns"] = round(
                sum(e.get("turns", 0) for e in entries_list) / len(entries_list), 2
            )

        # Deterministic claim violations for this arm. Always recorded, so the
        # report and the gate can say "0 flagged" rather than "not checked".
        # Errors are gated; warnings are reported only (see evaluation/claims.py).
        all_violations = [
            v for e in entries_list for v in (e.get("claim_violations") or [])
        ]
        model_summary["claim_violation_count"] = sum(
            1 for v in all_violations if v.get("severity") != claims.WARNING
        )
        warning_count = sum(
            1 for v in all_violations if v.get("severity") == claims.WARNING
        )
        if warning_count:
            model_summary["claim_warning_count"] = warning_count

        usages = [e["usage"] for e in entries_list if e.get("usage")]
        judge_usages = judge_usage.get(label, {})
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
            generation_model = label.removesuffix(_AGENT_LABEL_SUFFIX)
            costs = [_cost_usd(generation_model, u) for u in usages]
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

        summary["per_model"][label] = model_summary

    summary["entries"] = [
        {
            "id": m["entry_id"],
            "model": m["model"],
            "arm": m["arm"],
            "label": m["label"],
            "answer": m.get("answer"),
            "scores": {
                dim: {"score": s.get("score"), "explanation": s.get("explanation")}
                for dim, s in m["scores"].items()
            },
            "usage": m.get("usage"),
            "latency_ms": m.get("latency_ms"),
            "chunks_retrieved": m.get("chunks_retrieved"),
            "claim_violations": m.get("claim_violations") or [],
        }
        for model_results in results.values()
        for m in model_results
    ]

    return summary
