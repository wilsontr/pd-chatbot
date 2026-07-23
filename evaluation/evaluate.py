"""Evaluation harness CLI.

Usage:
    python -m evaluation.evaluate --all              # full eval
    python -m evaluation.evaluate --retrieval        # retrieval only
    python -m evaluation.evaluate --generation       # generation only
    python -m evaluation.evaluate --retrieval --no-ablation
    python -m evaluation.evaluate --generation --model claude-haiku-4-5
    python -m evaluation.evaluate --generation --compare claude-haiku-4-5
    python -m evaluation.evaluate --generation --compare deepseek-v4-flash deepseek-v4-pro  # cross-provider, needs DEEPSEEK_API_KEY
    python -m evaluation.evaluate --generation --judge gemini-3.1-pro-preview  # judge outside the compared vendor families, needs GEMINI_API_KEY
    python -m evaluation.evaluate --generation --trials 3  # repeat the full run N times, report mean/std across trials
    python -m evaluation.evaluate --generation --sample 5
    python -m evaluation.evaluate --retrieval --report EVAL.md
"""

import argparse
import datetime
import json
import statistics
import sys
from pathlib import Path
from typing import Any

from .dataset import GoldenDataset
from .retrieval import run_retrieval_eval
from .generation import run_generation_eval

_STRATEGY_LABELS = {
    "hybrid_rrf": "Hybrid RRF",
    "vector_only": "Vector-only",
    "bm25_only": "BM25-only",
}


def _aggregate_trials(trial_summaries: list[dict]) -> dict[str, Any]:
    """Merge N run_generation_eval summaries (same model set, same dataset) into
    one report with mean/std across trials for every numeric per-model field.

    Run-to-run variance matters here because generation isn't deterministic — the
    same model can score noticeably differently between runs (observed directly
    earlier in this project), so a single-trial comparison can overstate how
    confident a "model A beats model B" conclusion really is.
    """
    model_names = trial_summaries[0]["per_model"].keys()
    per_model: dict[str, Any] = {}
    for m_name in model_names:
        merged: dict[str, Any] = {}
        keys: set[str] = set()
        for t in trial_summaries:
            keys.update(t["per_model"].get(m_name, {}).keys())
        for key in keys:
            values = [
                t["per_model"].get(m_name, {}).get(key)
                for t in trial_summaries
                if isinstance(t["per_model"].get(m_name, {}).get(key), (int, float))
            ]
            if not values:
                continue
            if key == "n":
                merged["n"] = values[0]
                continue
            merged[f"{key}_by_trial"] = values
            merged[f"{key}_avg"] = round(sum(values) / len(values), 4)
            if len(values) > 1:
                merged[f"{key}_std"] = round(statistics.stdev(values), 4)
        per_model[m_name] = merged

    return {
        "trials": len(trial_summaries),
        "per_model": per_model,
        "raw_trials": trial_summaries,
    }


def main(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser(
        description="pd-chatbot evaluation harness"
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Run both retrieval and generation evaluation"
    )
    parser.add_argument(
        "--retrieval", action="store_true",
        help="Run retrieval evaluation (recall@k, MRR, nDCG)"
    )
    parser.add_argument(
        "--generation", action="store_true",
        help="Run generation evaluation (LLM-as-judge)"
    )
    parser.add_argument(
        "--no-ablation", action="store_true",
        help="Skip vector-only and BM25-only ablation (faster)"
    )
    parser.add_argument(
        "--model", type=str, default="claude-sonnet-4-6",
        help="Model for generation (default: claude-sonnet-4-6)"
    )
    parser.add_argument(
        "--compare", type=str, nargs="*",
        help="Additional models to compare against (e.g. --compare claude-haiku-4-5). "
             "A deepseek-* model routes to DeepSeek's API (requires DEEPSEEK_API_KEY)."
    )
    parser.add_argument(
        "--judge", type=str, default="claude-haiku-4-5",
        help="Model for LLM-as-judge scoring (default: claude-haiku-4-5)"
    )
    parser.add_argument(
        "--sample", type=int, default=None,
        help="Limit generation eval to N entries (default: all)"
    )
    parser.add_argument(
        "--trials", type=int, default=1,
        help="Repeat the full generation eval N times and report mean/std across "
             "trials, to distinguish real differences from run-to-run noise (default: 1)"
    )
    parser.add_argument(
        "--dataset", type=str, default=None,
        help="Path to golden dataset JSON (default: evaluation/golden_dataset.json)"
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Write results to JSON file"
    )
    parser.add_argument(
        "--report", type=str, default=None,
        help="Write a markdown report to the given path (e.g. --report EVAL.md)"
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress verbose output"
    )

    args = parser.parse_args(argv)

    if not args.all and not args.retrieval and not args.generation:
        parser.print_help()
        sys.exit(1)

    dataset_path = Path(args.dataset) if args.dataset else None
    dataset = GoldenDataset(dataset_path)

    if not args.quiet:
        stats = dataset.stats
        print(f"Dataset: {stats['total_entries']} entries "
              f"({stats['by_query_type']})", file=sys.stderr)

    results: dict = {}

    if args.all or args.retrieval:
        if not args.quiet:
            print("\n=== Retrieval Evaluation ===\n", file=sys.stderr)
        results["retrieval"] = run_retrieval_eval(
            dataset,
            ablation=not args.no_ablation,
            verbose=not args.quiet,
        )

    if args.all or args.generation:
        if not args.quiet:
            print("\n=== Generation Evaluation ===\n", file=sys.stderr)
        if args.trials > 1:
            trial_summaries = []
            for t in range(args.trials):
                if not args.quiet:
                    print(f"\n--- Trial {t+1}/{args.trials} ---\n", file=sys.stderr)
                trial_summaries.append(run_generation_eval(
                    dataset=dataset,
                    model=args.model,
                    judge_model=args.judge,
                    compare=args.compare,
                    sample=args.sample,
                    verbose=not args.quiet,
                ))
            results["generation"] = _aggregate_trials(trial_summaries)
        else:
            results["generation"] = run_generation_eval(
                dataset=dataset,
                model=args.model,
                judge_model=args.judge,
                compare=args.compare,
                sample=args.sample,
                verbose=not args.quiet,
            )

    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        if not args.quiet:
            print(f"\nResults written to {args.output}", file=sys.stderr)

    if args.report:
        report = _build_report(results, dataset.stats)
        with open(args.report, "w") as f:
            f.write(report)
        if not args.quiet:
            print(f"\nReport written to {args.report}", file=sys.stderr)

    return results


def _percent_change(old: float, new: float) -> str:
    if old == 0:
        return "N/A"
    pct = ((new - old) / old) * 100
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.0f}%"


def _build_report(results: dict, stats: dict) -> str:
    """Build a markdown report from evaluation results."""
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Evaluation Results",
        "",
        f"*Auto-generated by CI. Last updated: {now}*",
        "",
        "## Dataset",
        "",
        f"- **{stats['total_entries']} entries**: "
        f"{stats['by_query_type'].get('object_reference', 0)} object-reference, "
        f"{stats['by_query_type'].get('conceptual', 0)} conceptual, "
        f"{stats['by_query_type'].get('both', 0)} mixed, "
        f"{stats['multi_turn_count']} multi-turn",
        f"- Difficulty: {stats['by_difficulty']}",
        "",
    ]

    retrieval = results.get("retrieval", {})
    if retrieval:
        lines.append("## Retrieval")
        lines.append("")
        lines.append("| Strategy | recall@3 | recall@5 | recall@10 | MRR | nDCG@5 |")
        lines.append("|----------|----------|----------|-----------|-----|--------|")

        strategies = ["hybrid_rrf"]
        if retrieval.get("ablation"):
            strategies.extend(["vector_only", "bm25_only"])

        hybrid_recall5 = 0.0
        for name in strategies:
            data = retrieval.get(name, {})
            if not data:
                continue
            label = _STRATEGY_LABELS.get(name, name)
            recall = data.get("recall", {})
            ndcg = data.get("ndcg", {})
            line = (
                f"| **{label}** "
                f"| {recall.get('recall@3', 0):.2%} "
                f"| {recall.get('recall@5', 0):.2%} "
                f"| {recall.get('recall@10', 0):.2%} "
                f"| {data.get('mrr', 0):.3f} "
                f"| {ndcg.get('ndcg@5', 0):.3f} |"
            )
            lines.append(line)
            if name == "hybrid_rrf":
                hybrid_recall5 = recall.get("recall@5", 0)

        lines.append("")

        # Comparison summary
        if retrieval.get("ablation"):
            vector = retrieval.get("vector_only", {})
            bm25 = retrieval.get("bm25_only", {})
            v_r5 = vector.get("recall", {}).get("recall@5", 0)
            b_r5 = bm25.get("recall", {}).get("recall@5", 0)
            lines.append(
                f"**Hybrid RRF outperforms vector-only by "
                f"{_percent_change(v_r5, hybrid_recall5)} "
                f"and BM25-only by {_percent_change(b_r5, hybrid_recall5)} "
                f"on recall@5.**"
            )
            lines.append("")

    generation = results.get("generation", {})
    per_model = generation.get("per_model", {})
    if per_model:
        lines.append("## Generation (LLM-as-Judge)")
        lines.append("")
        lines.append("| Model | Faithfulness | Relevance | Citation | n |")
        lines.append("|-------|-------------|-----------|----------|---|")
        for model_name, mdata in per_model.items():
            f = mdata.get("faithfulness_mean", "-")
            r = mdata.get("relevance_mean", "-")
            c = mdata.get("citation_correctness_mean", "-")
            n = mdata.get("n", 0)
            lines.append(f"| {model_name} | {f} | {r} | {c} | {n} |")
        lines.append("")

    lines.append("---")
    lines.append(f"*Thresholds enforced in CI: recall@5 >= 60%, MRR >= 0.30, "
                 f"hybrid >= vector-only, hybrid >= BM25-only.*")

    return "\n".join(lines) + "\n"


def _format_scores_table(retrieval_result: dict) -> str:
    """Format retrieval results as a readable table."""
    lines = []
    strategies = ["hybrid_rrf"]
    if retrieval_result.get("ablation"):
        strategies.extend(["vector_only", "bm25_only"])

    header = f"{'Strategy':<16} {'recall@3':>10} {'recall@5':>10} {'recall@10':>10} {'MRR':>8} {'ndcg@5':>8}"
    lines.append(header)
    lines.append("-" * len(header))

    for name in strategies:
        data = retrieval_result.get(name, {})
        if not data:
            continue
        recall = data.get("recall", {})
        ndcg = data.get("ndcg", {})
        label = _STRATEGY_LABELS.get(name, name)
        line = (
            f"{label:<16} "
            f"{recall.get('recall@3', 0):>10.4f} "
            f"{recall.get('recall@5', 0):>10.4f} "
            f"{recall.get('recall@10', 0):>10.4f} "
            f"{data.get('mrr', 0):>8.4f} "
            f"{ndcg.get('ndcg@5', 0):>8.4f}"
        )
        lines.append(line)

    return "\n".join(lines)


if __name__ == "__main__":
    results = main()
    if "retrieval" in results:
        print(_format_scores_table(results["retrieval"]))
    print(json.dumps(results, indent=2, default=str))
