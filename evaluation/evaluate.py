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
    python -m evaluation.evaluate --retrieval --report docs/EVAL.md
    python -m evaluation.evaluate --generation --sample 10 --gate --summary "$GITHUB_STEP_SUMMARY"
    python -m evaluation.evaluate --generation --mode both --sample 10 --report docs/EVAL.md
        # pipeline vs agent on the same entries: the agent arm lets the model
        # choose its own retrieval tool calls, judged against what it retrieved
    python -m evaluation.evaluate --retrieval --agent-retrieval
        # CI gate: exits non-zero when a generation mean is below the documented
        # threshold, and appends sample/models/measured cost to the job summary
"""

import argparse
import datetime
import json
import statistics
import sys
from pathlib import Path
from typing import Any

from model_config import ANSWER_MODEL, JUDGE_MODEL
from .dataset import GoldenDataset
from .retrieval import run_retrieval_eval
from .generation import EVAL_MODES, run_generation_eval
from .scope_eval import run_scope_eval
from .gates import (
    GENERATION_THRESHOLDS,
    SCOPE_ADHERENCE_THRESHOLD,
    check_scope_threshold,
    check_generation_thresholds,
    generation_summary_markdown,
    mean_score,
)

_STRATEGY_LABELS = {
    "hybrid_rrf": "Hybrid RRF",
    "vector_only": "Vector-only",
    "bm25_only": "BM25-only",
    "agent_tool": "Agent tool calls",
}

_AGENT_SUFFIX = " (agent)"


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
        "--model", type=str, default=ANSWER_MODEL,
        help=f"Model for generation (default: {ANSWER_MODEL})"
    )
    parser.add_argument(
        "--compare", type=str, nargs="*",
        help="Additional models to compare against (e.g. --compare claude-haiku-4-5). "
             "A deepseek-* model routes to DeepSeek's API (requires DEEPSEEK_API_KEY)."
    )
    parser.add_argument(
        "--mode", type=str, default="pipeline", choices=list(EVAL_MODES),
        help="Generation arm(s): pipeline (fixed RAG, the CI gate), agent (tool-use "
             "loop) or both for a side-by-side comparison (default: pipeline)"
    )
    parser.add_argument(
        "--agent-retrieval", action="store_true",
        help="Also score the agent's search_docs tool calls in the retrieval eval "
             "(one model turn per entry, no answer generation)"
    )
    parser.add_argument(
        "--gate-agent", action="store_true",
        help="Apply the generation thresholds to agent arms too (off by default: "
             "the thresholds are calibrated on the pipeline)"
    )
    parser.add_argument(
        "--judge", type=str, default=JUDGE_MODEL,
        help=f"Model for LLM-as-judge scoring (default: {JUDGE_MODEL})"
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
        help="Write a markdown report to the given path (e.g. --report docs/EVAL.md)"
    )
    parser.add_argument(
        "--summary", type=str, default=None,
        help="Write a short markdown job summary to the given path "
             "(e.g. --summary \"$GITHUB_STEP_SUMMARY\"); states sample, models, "
             "measured cost and the gate verdict"
    )
    parser.add_argument(
        "--system-prompt-file", type=str, default=None,
        help="Score an alternative system prompt (e.g. a citation-rule variant) "
             "instead of the shipped one; the file is read as UTF-8 text"
    )
    parser.add_argument(
        "--gate", action="store_true",
        help="Exit non-zero when a generation mean falls below the documented "
             "threshold (faithfulness/relevance, see evaluation/gates.py)"
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

    prompt_text = None
    if args.system_prompt_file:
        prompt_text = Path(args.system_prompt_file).read_text(encoding="utf-8")
        if not args.quiet:
            print(f"System prompt: {args.system_prompt_file}", file=sys.stderr)

    results: dict = {}

    if args.all or args.retrieval:
        if not args.quiet:
            print("\n=== Retrieval Evaluation ===\n", file=sys.stderr)
        results["retrieval"] = run_retrieval_eval(
            dataset,
            ablation=not args.no_ablation,
            verbose=not args.quiet,
            agent=args.agent_retrieval,
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
                    system_prompt=prompt_text,
                    verbose=not args.quiet,
                    mode=args.mode,
                ))
            results["generation"] = _aggregate_trials(trial_summaries)
        else:
            results["generation"] = run_generation_eval(
                dataset=dataset,
                model=args.model,
                judge_model=args.judge,
                compare=args.compare,
                sample=args.sample,
                system_prompt=prompt_text,
                verbose=not args.quiet,
                mode=args.mode,
            )

    if "generation" in results:
        # Scope is scored with the same classifier the app uses, one call per
        # case and no generation, so it is cheap enough to run beside every
        # sampled generation eval.
        if not args.quiet:
            print("\n=== Scope Evaluation ===\n", file=sys.stderr)
        results["scope"] = run_scope_eval()

    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        if not args.quiet:
            print(f"\nResults written to {args.output}", file=sys.stderr)

    generation_meta = None
    if "generation" in results:
        generation_meta = {
            "model": args.model,
            "judge": args.judge,
            "sample": args.sample or len(dataset),
            "prompt": args.system_prompt_file or "default",
            "mode": args.mode,
        }

    if args.report:
        report = _build_report(results, dataset.stats, generation_meta=generation_meta)
        with open(args.report, "w") as f:
            f.write(report)
        if not args.quiet:
            print(f"\nReport written to {args.report}", file=sys.stderr)

    if args.summary:
        # Appended rather than overwritten: in CI this path is
        # $GITHUB_STEP_SUMMARY, which collects output from every step in the job.
        with open(args.summary, "a") as f:
            f.write(generation_summary_markdown(
                results,
                sample_size=args.sample or len(dataset),
                model=args.model,
                judge_model=args.judge,
                include_agent=args.gate_agent,
            ))
        if not args.quiet:
            print(f"\nJob summary appended to {args.summary}", file=sys.stderr)

    exit_code = 0
    if args.gate:
        if "generation" not in results:
            print("error: --gate requires --generation or --all", file=sys.stderr)
            return 2
        failures = check_generation_thresholds(
            results["generation"], include_agent=args.gate_agent
        )
        failures += check_scope_threshold(results.get("scope"))
        if failures:
            print("\nGeneration gate FAILED:", file=sys.stderr)
            for failure in failures:
                print(f"  - {failure}", file=sys.stderr)
            print(
                "Thresholds: "
                + ", ".join(
                    f"{dim} >= {minimum:g}"
                    for dim, minimum in GENERATION_THRESHOLDS.items()
                )
                + f", scope adherence >= {SCOPE_ADHERENCE_THRESHOLD:g}",
                file=sys.stderr,
            )
            exit_code = 1
        elif not args.quiet:
            print("\nGeneration gate passed.", file=sys.stderr)

    if not args.quiet:
        if "retrieval" in results:
            print(_format_scores_table(results["retrieval"]))
        print(json.dumps(results, indent=2, default=str))

    return exit_code


def _format_metric(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "-"


def _percent_change(old: float, new: float) -> str:
    if old == 0:
        return "N/A"
    pct = ((new - old) / old) * 100
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.0f}%"


def _build_report(results: dict, stats: dict, generation_meta: dict | None = None) -> str:
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
        if retrieval.get("agent_tool"):
            strategies.append("agent_tool")

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

        agent_tool = retrieval.get("agent_tool", {})
        if agent_tool:
            a_r5 = agent_tool.get("recall", {}).get("recall@5", 0)
            lines.append(
                f"**Agent tool calls vs. the pipeline's fixed retrieval: "
                f"recall@5 {a_r5:.2%} vs. {hybrid_recall5:.2%} "
                f"({_percent_change(hybrid_recall5, a_r5)}), "
                f"MRR {agent_tool.get('mrr', 0):.3f} vs. "
                f"{retrieval.get('hybrid_rrf', {}).get('mrr', 0):.3f}.** "
                "Each entry gets one model turn in which the model chooses its "
                "`search_docs` queries; the sections those calls return are scored."
            )
            lines.append("")

    generation = results.get("generation", {})
    per_model = generation.get("per_model", {})
    if per_model:
        lines.append("## Generation (LLM-as-Judge)")
        lines.append("")
        if generation_meta:
            prompt_note = ""
            if generation_meta.get("prompt") not in (None, "default"):
                prompt_note = f" System prompt variant: `{Path(generation_meta['prompt']).name}`."
            mode = generation_meta.get("mode", "pipeline")
            mode_note = {
                "both": " Both arms ran on every entry (pipeline and agent).",
                "agent": " Agent arm only: retrieval is a tool the model calls.",
            }.get(mode, "")
            lines.append(
                f"Sampled eval: {generation_meta['sample']} entries generated by "
                f"`{generation_meta['model']}`, scored 1-5 by `{generation_meta['judge']}`."
                + prompt_note + mode_note
            )
            lines.append("")
        has_agent = any(m.get("mode") == "agent" for m in per_model.values())
        header = "| Model | Faithfulness | Relevance | Citation | n | Cost/query | p50 latency |"
        divider = "|-------|-------------|-----------|----------|---|------------|-------------|"
        if has_agent:
            header += " Tool calls |"
            divider += "-----------|"
        lines.append(header)
        lines.append(divider)
        for model_name, mdata in per_model.items():
            f = _format_metric(mean_score(mdata, "faithfulness"))
            r = _format_metric(mean_score(mdata, "relevance"))
            c = _format_metric(mean_score(mdata, "citation_correctness"))
            n = mdata.get("n", 0)
            cost = mdata.get("avg_total_cost_usd")
            if cost is None:
                cost = mdata.get("avg_total_cost_usd_avg")
            cost_cell = f"${cost:.4f}" if isinstance(cost, (int, float)) else "-"
            p50 = mdata.get("p50_latency_ms")
            p50 = p50 if isinstance(p50, (int, float)) else None
            if p50 is None:
                p50 = mdata.get("p50_latency_ms_avg")
            p50_cell = f"{p50 / 1000:.1f}s" if isinstance(p50, (int, float)) else "-"
            row = (
                f"| {model_name} | {f} | {r} | {c} | {n} | {cost_cell} | {p50_cell} |"
            )
            if has_agent:
                tool_calls = mdata.get("avg_tool_calls")
                tool_calls = mdata.get("avg_tool_calls_avg", tool_calls)
                row += f" {tool_calls if isinstance(tool_calls, (int, float)) else '-'} |"
            lines.append(row)
        lines.append("")

        comparison = _agent_comparison(per_model)
        if comparison:
            lines.extend(comparison)

    scope_result = results.get("scope") or {}
    if scope_result.get("n"):
        lines.append("## Scope gate")
        lines.append("")
        lines.append(
            f"- **Adherence:** {scope_result['adherence']:.0%} of "
            f"{scope_result['n']} scope cases ({len(scope_result.get('failures') or [])} miss)"
        )
        for failure in (scope_result.get("failures") or [])[:5]:
            lines.append(
                f"- miss: `{failure['id']}` expected {failure['expected']}, "
                f"got {failure['predicted']}"
            )
        lines.append("")

    lines.append("---")
    if retrieval:
        lines.append(f"*Thresholds enforced in CI: recall@5 >= 60%, MRR >= 0.30, "
                     f"hybrid >= vector-only, hybrid >= BM25-only.*")
    if per_model:
        if retrieval:
            lines.append("")
        gate = ", ".join(
            f"{dim} >= {minimum:g}" for dim, minimum in GENERATION_THRESHOLDS.items()
        )
        lines.append(
            f"*Generation gate (mean on the 1-5 judge scale): {gate}, "
            f"scope adherence >= {SCOPE_ADHERENCE_THRESHOLD:g}. "
            "Citation correctness is gated: the prompt requires URLs copied "
            "verbatim from the excerpts, so invented URLs fail the build. "
            "The gate is enforced on the pipeline arm; agent-arm scores are "
            "reported alongside it (`--gate-agent` gates them too).*"
        )

    return "\n".join(lines) + "\n"


def _agent_comparison(per_model: dict) -> list[str]:
    """Pipeline-vs-agent rows when both arms ran on the same entries."""
    pairs = []
    for label, mdata in per_model.items():
        if mdata.get("mode") != "agent" and not label.endswith(" (agent)"):
            continue
        base_label = label.removesuffix(" (agent)")
        base = per_model.get(base_label)
        if base is None:
            continue
        pairs.append((base_label, base, label, mdata))
    if not pairs:
        return []

    def num(mdata: dict, *keys: str):
        for key in keys:
            value = mdata.get(key)
            if isinstance(value, (int, float)):
                return value
        return None

    def score(mdata: dict, dim: str) -> str:
        value = mean_score(mdata, dim)
        return f"{value:.2f}" if value is not None else "-"

    def cost(mdata: dict) -> str:
        value = num(mdata, "avg_total_cost_usd", "avg_total_cost_usd_avg")
        return f"${value:.4f}" if value is not None else "-"

    def p50(mdata: dict) -> str:
        value = num(mdata, "p50_latency_ms", "p50_latency_ms_avg")
        return f"{value / 1000:.1f}s" if value is not None else "-"

    def tools(mdata: dict) -> str:
        value = num(mdata, "avg_tool_calls", "avg_tool_calls_avg")
        return f"{value:g}" if value is not None else "0"

    lines = [
        "### Pipeline vs agent",
        "",
        "Same entries, same judge, same 1-5 rubric. The agent arm decides its own "
        "`search_docs` calls; the pipeline arm retrieves once and generates.",
        "",
        "| Arm | Faithfulness | Relevance | Citation | n | Cost/query | p50 latency | Tool calls |",
        "|-----|--------------|-----------|----------|---|------------|-------------|-----------|",
    ]
    for base_label, base, agent_label, agent in pairs:
        for label, mdata in ((f"{base_label} (pipeline)", base), (agent_label, agent)):
            lines.append(
                f"| {label} | {score(mdata, 'faithfulness')} "
                f"| {score(mdata, 'relevance')} "
                f"| {score(mdata, 'citation_correctness')} "
                f"| {mdata.get('n', 0)} | {cost(mdata)} | {p50(mdata)} "
                f"| {tools(mdata)} |"
            )
    lines.append("")
    for base_label, base, agent_label, agent in pairs:
        deltas = []
        for dim, human in (
            ("faithfulness", "faithfulness"),
            ("relevance", "relevance"),
            ("citation_correctness", "citation"),
        ):
            b, a = mean_score(base, dim), mean_score(agent, dim)
            if b is not None and a is not None:
                deltas.append(f"{human} {a - b:+.2f}")
        if deltas:
            skipped = agent.get("skipped_no_context")
            skipped_note = (
                f"; {skipped} entr{'y' if skipped == 1 else 'ies'} skipped with no chunks"
                if skipped else ""
            )
            lines.append(
                f"- **{base_label}**: " + ", ".join(deltas)
                + f" (agent \u2212 pipeline){skipped_note}."
            )
    lines.append("")
    return lines


def _format_scores_table(retrieval_result: dict) -> str:
    """Format retrieval results as a readable table."""
    lines = []
    strategies = ["hybrid_rrf"]
    if retrieval_result.get("ablation"):
        strategies.extend(["vector_only", "bm25_only"])
    if retrieval_result.get("agent_tool"):
        strategies.append("agent_tool")

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
    sys.exit(main())
