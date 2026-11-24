"""Quality gates for the sampled generation eval (TASK-3).

The retrieval gate lives in ``tests/test_evaluation.py``. Generation needs its
own gate because every run costs money, so it is sampled, run from CI rather
than from pytest, and fails the build on a regression instead of asserting a
threshold after the fact.

Baseline measured 2026-11-24 with the shipping configuration (claude-haiku-5-5
with thinking disabled, claude-haiku-4-5 judge, 1-5 rubric), 30 entries on one
trial: faithfulness 4.63, relevance 4.87, citation correctness 4.50; enable
model-side thinking and citation rises to 4.58 with faithfulness 4.92. Identical
runs move a mean by up to ~0.1, so the thresholds sit a clear margin below the
observed means: tight enough to catch a prompt change that makes answers less
grounded, loose enough that judge noise alone does not fail a PR.

Citation correctness is gated as a *collapse guard*, not as a precision
instrument. It used to be reported only, because it is the noisiest dimension
(individual entries score as low as 2). Once the prompt was hardened so URLs
must be copied verbatim from the excerpts, the 30-entry mean moved from
4.37-4.48 to 4.58. On the 10-entry CI prefix the same configuration measures
4.40, and with 10 integer-scored entries a mean moves ~0.2 run-to-run, so a
threshold close to 4.40 would be flaky and one close to the old 4.4 baseline
would not detect the regression at all. The 4.0 threshold is therefore a floor
that catches citation collapse (invented or missing URLs driving scores toward
3); the 30-entry comparison in docs/MODEL_COMPARISON.md remains the precise
instrument for citation quality.

This module is deliberately dependency-free (no rag, chroma or API clients) so
the gate logic is unit-testable without keys.
"""

from __future__ import annotations

from typing import Any, Mapping

from model_config import ANSWER_MODEL, JUDGE_MODEL

# Means on the 1-5 judge scale below which the build fails. 30-entry baselines
# (2026-11-24) are 4.92 / 4.87 / 4.58; the 10-entry CI prefix measures lower for
# citation (4.40), which is why that threshold is a floor rather than a tight
# bound — see the module docstring.
GENERATION_THRESHOLDS: dict[str, float] = {
    "faithfulness": 4.0,
    "relevance": 4.5,
    "citation_correctness": 4.0,
}

# The CI gate is bounded by design: cost scales linearly with the sample.
CI_SAMPLE_SIZE = 10
CI_MODEL = ANSWER_MODEL
CI_JUDGE_MODEL = JUDGE_MODEL


def _mean_key(dimension: str) -> str:
    return f"{dimension}_mean"


def mean_score(model_summary: Mapping[str, Any], dimension: str) -> float | None:
    """Mean for one dimension, tolerating the multi-trial summary shape.

    ``--trials N`` merges runs through ``_aggregate_trials``, which suffixes every
    numeric field with ``_avg`` (``faithfulness_mean_avg``). Reading both shapes
    keeps the gate and the report working for either.
    """
    for key in (_mean_key(dimension), f"{_mean_key(dimension)}_avg"):
        value = model_summary.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def check_generation_thresholds(
    generation: Mapping[str, Any] | None,
    thresholds: Mapping[str, float] | None = None,
) -> list[str]:
    """Return one message per gated dimension that is missing or below threshold.

    An empty list means the gate passed. ``generation`` is the dict returned by
    ``run_generation_eval`` (``{"per_model": {model: {...}}}``).
    """
    thresholds = GENERATION_THRESHOLDS if thresholds is None else thresholds
    per_model = (generation or {}).get("per_model", {}) or {}
    if not per_model:
        return ["no generation scores were produced (is the sample empty?)"]

    failures: list[str] = []
    for model_name, model_summary in per_model.items():
        if not model_summary.get("n"):
            failures.append(f"{model_name}: no entries were scored")
            continue
        for dimension, minimum in thresholds.items():
            score = mean_score(model_summary, dimension)
            if score is None:
                failures.append(
                    f"{model_name}: {dimension} has no usable score "
                    "(every judge call failed or was dropped)"
                )
            elif score < minimum:
                failures.append(
                    f"{model_name}: {dimension} {score:.2f} < {minimum:.2f} threshold"
                )
    return failures


def _format_cost(model_summary: Mapping[str, Any]) -> str:
    for key in ("avg_total_cost_usd", "avg_total_cost_usd_avg", "avg_cost_usd"):
        value = model_summary.get(key)
        if isinstance(value, (int, float)):
            return f"${value:.4f}"
    return "n/a"


def _format_score(value: Any) -> str:
    return f"{value:.2f}" if isinstance(value, (int, float)) else "-"


def generation_summary_markdown(
    results: Mapping[str, Any],
    *,
    sample_size: int,
    model: str = CI_MODEL,
    judge_model: str = CI_JUDGE_MODEL,
    thresholds: Mapping[str, float] | None = None,
) -> str:
    """Build the job-summary markdown for a sampled generation run.

    Writes into ``$GITHUB_STEP_SUMMARY`` in CI, so it states the cost bound
    (sample size, models, measured dollars) next to the scores and the verdict.
    """
    thresholds = GENERATION_THRESHOLDS if thresholds is None else thresholds
    generation = results.get("generation", {}) or {}
    per_model = generation.get("per_model", {}) or {}
    # No scores at all is a failure: a run that produced nothing must not pass.
    failures = check_generation_thresholds(generation, thresholds)

    lines: list[str] = [
        "## Generation eval (sampled)",
        "",
        f"- **Generation model:** `{model}`",
        f"- **Judge model:** `{judge_model}` (1-5 rubric)",
        f"- **Sample:** {sample_size} golden entries (deterministic prefix)",
        f"- **Gate:** "
        + ", ".join(f"{dim} >= {minimum:g}" for dim, minimum in thresholds.items()),
        "",
    ]

    if per_model:
        lines += [
            "| Model | Faithfulness | Relevance | Citation | n | Cost/query |",
            "|-------|--------------|-----------|----------|---|------------|",
        ]
        for model_name, m in per_model.items():
            lines.append(
                f"| {model_name} "
                f"| {_format_score(mean_score(m, 'faithfulness'))} "
                f"| {_format_score(mean_score(m, 'relevance'))} "
                f"| {_format_score(mean_score(m, 'citation_correctness'))} "
                f"| {m.get('n', 0)} "
                f"| {_format_cost(m)} |"
            )
        lines.append("")

        run_cost = sum(
            m.get("run_total_cost_usd") or 0.0 for m in per_model.values()
        )
        if run_cost:
            lines.append(
                f"Measured cost for this run: **${run_cost:.4f}** "
                "(generation + judge tokens; retrieval calls are extra)."
            )
            lines.append("")

    if failures:
        lines.append("### Gate: FAILED")
        lines.append("")
        lines += [f"- {message}" for message in failures]
    else:
        lines.append("### Gate: passed")
        if per_model:
            lines.append("")
            for model_name, m in per_model.items():
                for dimension, minimum in thresholds.items():
                    lines.append(
                        f"- {model_name}: {dimension} "
                        f"{_format_score(mean_score(m, dimension))} "
                        f">= {minimum:g}"
                    )
    lines.append("")
    return "\n".join(lines)
