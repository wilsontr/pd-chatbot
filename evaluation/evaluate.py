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
from . import claims
from .dataset import GoldenDataset
from .retrieval import run_retrieval_eval
from .generation import EVAL_MODES, run_generation_eval
from .scope_eval import run_scope_eval
from .gates import (
    AGENT_THRESHOLDS,
    GENERATION_THRESHOLDS,
    SCOPE_ADHERENCE_THRESHOLD,
    check_agent_thresholds,
    check_claim_violations,
    check_scope_threshold,
    check_generation_thresholds,
    format_agent_thresholds,
    generation_summary_markdown,
    is_agent_arm,
    mean_score,
    recovery_stats,
    skills_summary_markdown,
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

    Non-numeric fields (``mode``) are carried over from the first trial: the
    report and the gate dispatch on ``mode`` to tell the agent arm from the
    pipeline arm, so dropping it silently loses the agent columns and the
    Pipeline-vs-agent table on a multi-trial run.
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
                carried = [
                    t["per_model"].get(m_name, {}).get(key) for t in trial_summaries
                ]
                carried = [value for value in carried if value is not None]
                if carried:
                    merged[key] = carried[0]
                continue
            if key == "n":
                # A trial that skipped an entry (no chunks retrieved) scores one
                # fewer entry; report the mean rather than the first trial's count.
                merged["n"] = (
                    values[0]
                    if len(set(values)) == 1
                    else round(sum(values) / len(values))
                )
                merged["n_by_trial"] = values
                continue
            merged[f"{key}_by_trial"] = values
            merged[f"{key}_avg"] = round(sum(values) / len(values), 4)
            if len(values) > 1:
                merged[f"{key}_std"] = round(statistics.stdev(values), 4)
        per_model[m_name] = merged

    # Flatten the per-entry records so the report and the claim gate keep their
    # per-entry detail (a violation, a low-scoring entry) on a multi-trial run.
    entries: list[dict[str, Any]] = [
        {**entry, "trial": index + 1}
        for index, summary in enumerate(trial_summaries)
        for entry in (summary.get("entries") or [])
    ]

    return {
        "trials": len(trial_summaries),
        "per_model": per_model,
        "entries": entries,
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
        help="Enforce generation and agent-level thresholds (task success, tool calls, "
             "cost, p95 latency, recovery rate) on agent arms too"
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
             "threshold, or when a deterministic claim check fires "
             "(see evaluation/gates.py and evaluation/claims.py)"
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
            "trials": args.trials,
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
        if args.gate_agent:
            failures += check_agent_thresholds(results["generation"])
        failures += check_scope_threshold(results.get("scope"))
        claim_failures = check_claim_violations(results["generation"])
        if failures or claim_failures:
            print("\nGeneration gate FAILED:", file=sys.stderr)
            for failure in failures:
                print(f"  - {failure}", file=sys.stderr)
            for failure in claim_failures:
                print(f"  - claim check: {failure}", file=sys.stderr)
            threshold_msg = (
                "Thresholds: "
                + ", ".join(
                    f"{dim} >= {minimum:g}"
                    for dim, minimum in GENERATION_THRESHOLDS.items()
                )
                + f", scope adherence >= {SCOPE_ADHERENCE_THRESHOLD:g}"
            )
            if args.gate_agent:
                threshold_msg += f"; agent (docs mode): {format_agent_thresholds(AGENT_THRESHOLDS)}"
            print(threshold_msg, file=sys.stderr)
            exit_code = 1
        elif not args.quiet:
            print("\nGeneration gate passed.", file=sys.stderr)

    if not args.quiet:
        if "retrieval" in results:
            print(_format_scores_table(results["retrieval"]))
        print(json.dumps(results, indent=2, default=str))

    return exit_code


def _metric(model_summary: dict, *keys: str) -> float | None:
    """First numeric value under a single-trial key or its multi-trial ``_avg``."""
    for key in keys:
        for name in (key, f"{key}_avg"):
            value = model_summary.get(name)
            if isinstance(value, (int, float)):
                return float(value)
    return None


def _spread(model_summary: dict, key: str) -> float | None:
    """Standard deviation across trials for a merged numeric field, if any."""
    value = model_summary.get(f"{key}_std")
    return float(value) if isinstance(value, (int, float)) else None


def _score_cell(model_summary: dict, dimension: str) -> str:
    mean = mean_score(model_summary, dimension)
    if mean is None:
        return "-"
    std = _spread(model_summary, f"{dimension}_mean")
    return f"{mean:.2f} ± {std:.2f}" if std is not None else f"{mean:.2f}"


def _cost_cell(model_summary: dict) -> str:
    value = _metric(model_summary, "avg_total_cost_usd")
    return f"${value:.4f}" if value is not None else "-"


def _seconds_cell(model_summary: dict, key: str) -> str:
    value = _metric(model_summary, key)
    return f"{value / 1000:.1f}s" if value is not None else "-"


def _tool_cell(model_summary: dict) -> str:
    value = _metric(model_summary, "avg_tool_calls")
    if value is None:
        return "0"
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}"


def _task_success_cell(model_summary: dict) -> str:
    value = _metric(model_summary, "task_success_rate")
    if value is None:
        return "-"
    std = _spread(model_summary, "task_success_rate")
    return f"{value:.0%} ± {std:.0%}" if std is not None else f"{value:.0%}"


def _recovery_cell(model_name: str, model_summary: dict) -> str:
    if not is_agent_arm(model_name, model_summary):
        return "-"
    rate, recovered, errors = recovery_stats(model_summary)
    if errors > 0 and rate is not None:
        return f"{rate:.0%} ({recovered}/{errors})"
    if rate is not None:
        return f"{rate:.0%}"
    return "n/a (0 errors)"


def _cost_total_lines(per_model: dict, trials: int) -> list[str]:
    """The measured dollar cost of the run, next to the per-query cost."""
    per_trial = sum(
        _metric(mdata, "run_total_cost_usd") or 0.0 for mdata in per_model.values()
    )
    if not per_trial:
        return []
    total = per_trial * trials
    arms = len(per_model)
    suffix = f" across {trials} trials" if trials > 1 else ""
    return [
        f"- **Measured cost:** ${total:.4f} for this run{suffix} "
        f"({arms} arm{'s' if arms != 1 else ''}: generation + judge tokens; "
        "retrieval embedding calls are extra).",
        "",
    ]


def _percent_change(old: float, new: float) -> str:
    if old == 0:
        return "N/A"
    pct = ((new - old) / old) * 100
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.0f}%"


#: The routine report every push to main rewrites, and the multi-trial baseline
#: (TASK-5.38) that only a manual run with --trials >= 3 writes. The README's
#: agent-vs-pipeline figures cite the baseline.
ROUTINE_REPORT = Path("docs/EVAL.md")
BASELINE_REPORT = Path("docs/EVAL_BASELINE.md")
BASELINE_MIN_TRIALS = 3


def _sample_lines(results: dict, stats: dict, generation_meta: dict | None) -> list[str]:
    """What this report measured, stated before any table."""
    generation = results.get("generation") or {}
    if not generation.get("per_model"):
        return [
            f"**This report:** retrieval on all {stats['total_entries']} entries, one run.",
            "",
        ]
    sample = (generation_meta or {}).get("sample") or stats["total_entries"]
    trials = generation.get("trials") or (generation_meta or {}).get("trials") or 1
    line = (
        f"**This report:** generation on {sample} entries x {trials} "
        f"trial{'s' if trials != 1 else ''} per arm; retrieval on all "
        f"{stats['total_entries']} entries, one run."
    )
    if trials < BASELINE_MIN_TRIALS:
        line += (
            " This is the routine sample run on every push to main. The published "
            f"agent-vs-pipeline baseline ({BASELINE_MIN_TRIALS}+ trials) is "
            f"[{BASELINE_REPORT.name}]({BASELINE_REPORT.name})."
        )
    return [line, ""]


def _build_report(results: dict, stats: dict, generation_meta: dict | None = None) -> str:
    """Build a markdown report from evaluation results."""
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Evaluation Results",
        "",
        f"*Auto-generated by CI. Last updated: {now}*",
        "",
        *_sample_lines(results, stats, generation_meta),
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
        lines.append(
            f"All {stats['total_entries']} entries, one run. The pipeline strategies are "
            "deterministic; the agent row comes from one model turn per entry."
        )
        lines.append("")
        lines.append("| Strategy | recall@3 | recall@5 | recall@10 | MRR | nDCG@5 | nDCG@10 |")
        lines.append("|----------|----------|----------|-----------|-----|--------|---------|")

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
                f"| {ndcg.get('ndcg@5', 0):.3f} "
                f"| {ndcg.get('ndcg@10', 0):.3f} |"
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
            trials = generation.get("trials") or generation_meta.get("trials") or 1
            trial_note = (
                f" {trials} trials per arm; cells are the mean ± the standard "
                "deviation across trials."
                if trials > 1
                else " 1 trial per arm; cells are single-run means."
            )
            lines.append(
                f"Sampled eval: {generation_meta['sample']} entries generated by "
                f"`{generation_meta['model']}`, scored 1-5 by `{generation_meta['judge']}`."
                + prompt_note + mode_note + trial_note
            )
            lines.append(
                "The judge's Pd inlet/outlet-ordering belief is pinned in both "
                "directions by control answers in "
                "[`evaluation/judge_controls.py`](../evaluation/judge_controls.py)."
            )
            lines.append("")
        trials = generation.get("trials") or 1
        has_agent = any(is_agent_arm(name, m) for name, m in per_model.items())
        has_task_metrics = has_agent or any(
            _metric(m, "task_success_rate") is not None for m in per_model.values()
        )
        header = ("| Model | Faithfulness | Relevance | Citation | n | Cost/query "
                  "| p50 latency | p95 latency |")
        divider = ("|-------|-------------|-----------|----------|---|------------|"
                   "-------------|-------------|")
        if has_agent:
            header += " Tool calls |"
            divider += "-----------|"
        if has_task_metrics:
            header += " Task success |"
            divider += "--------------|"
        if has_agent:
            header += " Recovery |"
            divider += "----------|"
        lines.append(header)
        lines.append(divider)
        for model_name, mdata in per_model.items():
            row = (
                f"| {model_name} | {_score_cell(mdata, 'faithfulness')} "
                f"| {_score_cell(mdata, 'relevance')} "
                f"| {_score_cell(mdata, 'citation_correctness')} "
                f"| {mdata.get('n', 0)} | {_cost_cell(mdata)} "
                f"| {_seconds_cell(mdata, 'p50_latency_ms')} "
                f"| {_seconds_cell(mdata, 'p95_latency_ms')} |"
            )
            if has_agent:
                row += f" {_tool_cell(mdata)} |"
            if has_task_metrics:
                row += f" {_task_success_cell(mdata)} |"
            if has_agent:
                row += f" {_recovery_cell(model_name, mdata)} |"
            lines.append(row)
        lines.append("")
        lines.extend(_cost_total_lines(per_model, trials))

        comparison = _agent_comparison(per_model, trials)
        if comparison:
            lines.extend(comparison)
        lines.extend(_claim_check_section(generation))

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

    skills_section = skills_summary_markdown()
    if skills_section:
        lines.extend(skills_section.splitlines())

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
            f"Agent gate (CI `--gate-agent`, docs mode, no execution): "
            f"{format_agent_thresholds(AGENT_THRESHOLDS)}; execution mode is gated by "
            "the patch eval (docs/PATCH_EVAL.md). "
            "Deterministic claim checks fail the gate for every arm.*"
        )

    return "\n".join(lines) + "\n"


def _agent_comparison(per_model: dict, trials: int = 1) -> list[str]:
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

    n = max(int(m.get("n") or 0) for _, m, _, _ in pairs)
    if trials > 1:
        sample = f"**{n} entries x {trials} trials per arm.** "
    else:
        sample = (
            f"**{n} entries x 1 trial per arm**: too few runs to call a difference "
            f"between the arms; the multi-trial comparison is in "
            f"[{BASELINE_REPORT.name}]({BASELINE_REPORT.name}). "
        )
    lines = [
        "### Pipeline vs agent",
        "",
        sample
        + "Same entries, same judge, same 1-5 rubric. The agent arm decides its own "
        "`search_docs` calls; the pipeline arm retrieves once and generates. "
        "Task success counts entries scoring >= 4 on every judge dimension with "
        "0 error claim violations; recovery is the share of error-encountering "
        "agent tasks (tool failure, empty search, or non-clean Pd run) that "
        "still recovered and passed.",
        "",
        "| Arm | Faithfulness | Relevance | Citation | n | Cost/query | p50 latency "
        "| p95 latency | Tool calls | Task success | Recovery |",
        "|-----|--------------|-----------|----------|---|------------|-------------|"
        "-------------|-----------|--------------|----------|",
    ]
    for base_label, base, agent_label, agent in pairs:
        for label, mdata in ((f"{base_label} (pipeline)", base), (agent_label, agent)):
            lines.append(
                f"| {label} | {_score_cell(mdata, 'faithfulness')} "
                f"| {_score_cell(mdata, 'relevance')} "
                f"| {_score_cell(mdata, 'citation_correctness')} "
                f"| {mdata.get('n', 0)} | {_cost_cell(mdata)} "
                f"| {_seconds_cell(mdata, 'p50_latency_ms')} "
                f"| {_seconds_cell(mdata, 'p95_latency_ms')} "
                f"| {_tool_cell(mdata)} "
                f"| {_task_success_cell(mdata)} "
                f"| {_recovery_cell(label, mdata)} |"
            )
    lines.append("")
    for base_label, base, agent_label, agent in pairs:
        lines.extend(_parity_reading(base_label, base, agent))
    lines.append("")
    return lines


#: A per-dimension difference below this is not meaningful on a 1-5, integer-
#: scored rubric even when it happens to be larger than the trial spread.
_MIN_MEANINGFUL_DELTA = 0.1


def _paired_delta(
    base: dict, agent: dict, dimension: str
) -> tuple[float, float | None, list[float]] | None:
    """Agent-minus-pipeline difference for one dimension.

    Repeated trials give one delta per trial; the mean and the standard deviation
    of those deltas are what the report publishes, because a delta with no spread
    next to it is a single-sample claim.
    """
    key = f"{dimension}_mean"
    base_trials = base.get(f"{key}_by_trial")
    agent_trials = agent.get(f"{key}_by_trial")
    if (
        isinstance(base_trials, list)
        and isinstance(agent_trials, list)
        and len(base_trials) == len(agent_trials)
        and len(base_trials) > 1
    ):
        deltas = [round(a - b, 4) for b, a in zip(base_trials, agent_trials)]
        mean = sum(deltas) / len(deltas)
        return mean, statistics.stdev(deltas), deltas
    base_mean = mean_score(base, dimension)
    agent_mean = mean_score(agent, dimension)
    if base_mean is None or agent_mean is None:
        return None
    return agent_mean - base_mean, None, []


def _parity_reading(base_label: str, base: dict, agent: dict) -> list[str]:
    """The delta line plus a verdict, derived from the numbers rather than typed.

    The verdict is deliberately conservative: a dimension counts as resolved only
    when the mean difference is at least 0.1 *and* larger than its own spread
    across trials. Otherwise the honest statement is that the difference is not
    resolvable at this sample size.
    """
    readings: list[tuple[str, float, float | None]] = []
    for dimension, human in (
        ("faithfulness", "faithfulness"),
        ("relevance", "relevance"),
        ("citation_correctness", "citation"),
    ):
        paired = _paired_delta(base, agent, dimension)
        if paired is None:
            continue
        mean, std, _deltas = paired
        readings.append((human, mean, std))
    if not readings:
        return []

    deltas_text = ", ".join(
        f"{human} {mean:+.2f}" + (f" ± {std:.2f}" if std is not None else "")
        for human, mean, std in readings
    )
    resolved = [
        (human, mean)
        for human, mean, std in readings
        if abs(mean) >= _MIN_MEANINGFUL_DELTA and (std is None or abs(mean) > std)
    ]
    if not resolved:
        verdict = (
            "the agent matches the pipeline on every scored dimension — no "
            "difference is both larger than the trial-to-trial spread and at "
            "least 0.1 on the 1-5 scale"
        )
    elif all(mean > 0 for _human, mean in resolved):
        verdict = "the agent beats the pipeline on " + ", ".join(
            human for human, _ in resolved
        )
    elif all(mean < 0 for _human, mean in resolved):
        verdict = "the agent trails the pipeline on " + ", ".join(
            human for human, _ in resolved
        )
    else:
        verdict = "the agent is mixed against the pipeline (" + ", ".join(
            f"{human} {mean:+.2f}" for human, mean in resolved
        ) + ")"

    spread_note = (
        "Standard deviations are of the per-trial differences; a difference is "
        "called real only when it exceeds both its own spread and 0.1."
        if any(std is not None for _human, _mean, std in readings)
        else ""
    )
    skipped = _metric(agent, "skipped_no_context")
    skipped_note = (
        f"; {int(skipped)} entr{'y' if skipped == 1 else 'ies'} skipped with no chunks"
        if skipped else ""
    )
    lines = [
        f"- **{base_label}**: {deltas_text} (agent \u2212 pipeline){skipped_note}.",
        f"- **Reading:** {verdict}. {spread_note}".rstrip(),
    ]
    return lines


def _claim_check_section(generation: dict) -> list[str]:
    """Deterministic claim checks: what the judge cannot be relied on to catch."""
    entries = generation.get("entries") or []
    if not entries:
        return []
    grouped: dict[str, list[dict]] = {}
    seen: set[tuple] = set()
    for entry in entries:
        label = str(entry.get("label") or "?")
        for violation in entry.get("claim_violations") or []:
            key = (
                label,
                violation.get("entry_id"),
                violation.get("check"),
                violation.get("evidence"),
            )
            if key in seen:
                continue
            seen.add(key)
            grouped.setdefault(label, []).append(dict(violation))

    scored = len({(e.get("label"), e.get("id")) for e in entries})
    lines = [
        "### Deterministic claim checks",
        "",
        "Offline checks run on every scored answer "
        "([`evaluation/claims.py`](../evaluation/claims.py)): a list that fills "
        "inlets right to left, each pinned entry's recorded wrong claim, an object "
        "discussed without an excerpt covering it, and a cited URL no excerpt "
        "contains. They do not depend on the judge, whose inlet/outlet-ordering "
        "belief is itself checked in both directions against recorded answers "
        "([`evaluation/judge_controls.py`](../evaluation/judge_controls.py)).",
        "",
    ]
    if not grouped:
        lines.append(f"- **0 flagged** across {scored} scored answers.")
    else:
        everything = [item for items in grouped.values() for item in items]
        errors = sum(1 for item in everything if item.get("severity") != claims.WARNING)
        warnings = len(everything) - errors
        counted = f"{errors} error(s), {warnings} warning(s)"
        lines.append(f"- **{len(everything)} flagged** across {scored} scored answers "
                     f"({counted}; errors fail the gate, warnings are published only):")
        for label, items in grouped.items():
            for item in items:
                marker = " ⚠" if item.get("severity") == claims.WARNING else ""
                lines.append(f"  - `{label}`{marker} {claims.describe(item)}")
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

    header = (f"{'Strategy':<16} {'recall@3':>10} {'recall@5':>10} {'recall@10':>10} "
              f"{'MRR':>8} {'ndcg@5':>8} {'ndcg@10':>8}")
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
            f"{ndcg.get('ndcg@5', 0):>8.4f} "
            f"{ndcg.get('ndcg@10', 0):>8.4f}"
        )
        lines.append(line)

    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
