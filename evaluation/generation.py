"""Generation evaluation: produce answers and score them via LLM-as-judge.

Supports:
  - Single-model evaluation (default Sonnet)
  - Model comparison (Sonnet vs Haiku vs any model)
  - Subset sampling (evaluating every question burns tokens)
"""

import logging
from typing import Any

import anthropic

from .dataset import GoldenDataset
from .judge import evaluate_generation, compare_models
from rag import (
    retrieve,
    _build_chat_messages,
    SYSTEM_PROMPT,
    child_chunks as _,
    parent_lookup as __,
)

logger = logging.getLogger(__name__)


def _generate(
    client: anthropic.Anthropic,
    model: str,
    question: str,
    context_chunks: list[dict[str, Any]],
    history: list[dict[str, str]] | None = None,
) -> str:
    """Generate a response with a specific model."""
    if history is None:
        history = []
    system_block, messages = _build_chat_messages(question, context_chunks, history)
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system_block,
        messages=messages,
    )
    return response.content[0].text


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
        for m in models_to_run:
            try:
                answer = _generate(client, m, entry.question, chunks, entry.history)
                model_answers[m] = answer
                if verbose:
                    print(f"  [{m}] generated ({len(answer)} chars)")
            except Exception as exc:
                logger.error("Generation failed for %s with %s: %s", entry.id, m, exc)
                model_answers[m] = f"[ERROR: {exc}]"

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
            if scores:
                model_summary[f"{dim}_mean"] = round(sum(scores) / len(scores), 2)
                model_summary[f"{dim}_min"] = min(scores)
                model_summary[f"{dim}_max"] = max(scores)
        summary["per_model"][m_name] = model_summary

    summary["entries"] = [
        {
            "id": m["entry_id"],
            "model": m["model"],
        }
        for model_results in results.values()
        for m in model_results
    ]

    return summary
