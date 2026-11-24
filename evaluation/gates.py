"""Quality gates for the sampled generation eval (TASK-3).

The retrieval gate lives in ``tests/test_evaluation.py``. Generation needs its
own gate because every run costs money, so it is sampled, run from CI rather
than from pytest, and fails the build on a regression instead of asserting a
threshold after the fact.

Baseline measured 2026-11-24 with the shipping configuration (claude-haiku-5-5
with thinking disabled, claude-haiku-4-5 judge, 1-5 rubric), 30 entries x 3
trials: faithfulness 4.63 ± 0.14, relevance 4.81 ± 0.02, citation correctness
4.50 ± 0.09. That baseline is *after* the TASK-5.22 judge-rubric correction
(the judge now states Pd's inlet/outlet ordering rule and is controlled in both
directions by ``evaluation/judge_controls.py``), the TASK-5.23 object-attribution
prompt rules and the TASK-5.24 pd-patch count canonicalisation; the changes moved
per-entry scores by up to two points while leaving the gate decision unchanged.
Identical runs move a mean by up to ~0.1 and single entries by much more, so the
thresholds sit a clear margin below the observed means: tight enough to catch a
prompt change that makes answers less grounded, loose enough that judge noise
alone does not fail a PR.

Citation correctness is gated as a *collapse guard*, not as a precision
instrument. It used to be reported only, because it is the noisiest dimension
(individual entries score as low as 2, and the same entry has moved 2 ↔ 5
between trials of one run). Once the prompt was hardened so URLs must be copied
verbatim from the excerpts, the 30-entry mean moved from 4.37-4.48 to ~4.5-4.6.
On the 10-entry CI prefix the same configuration measures lower, and with 10
integer-scored entries a mean moves ~0.2 run-to-run, so a threshold close to
that would be flaky and one close to the old 4.4 baseline would not detect the
regression at all. The 4.0 threshold is therefore a floor that catches citation
collapse (invented or missing URLs driving scores toward 3); the 30-entry
comparison in docs/EVAL.md and docs/MODEL_COMPARISON.md remains the precise
instrument for citation quality.

This module is deliberately dependency-free (no rag, chroma or API clients) so
the gate logic is unit-testable without keys.
"""

from __future__ import annotations

from typing import Any, Mapping

from model_config import ANSWER_MODEL, JUDGE_MODEL

from . import claims

# Means on the 1-5 judge scale below which the build fails. The 30-entry
# baselines (2026-11-24, corrected rubric) are 4.63 / 4.81 / 4.50; the 10-entry
# CI prefix measures lower for citation, which is why that threshold is a floor
# rather than a tight bound — see the module docstring.
GENERATION_THRESHOLDS: dict[str, float] = {
    "faithfulness": 4.0,
    "relevance": 4.5,
    "citation_correctness": 4.0,
}

# The CI gate is bounded by design: cost scales linearly with the sample.
CI_SAMPLE_SIZE = 10
CI_MODEL = ANSWER_MODEL
CI_JUDGE_MODEL = JUDGE_MODEL

# Fraction of the scope dataset the gate must handle as expected. The dataset has
# 20 cases (6 off-topic, 3 mixed, 6 policy, 2 multi-turn bypass, 3 on-topic), so
# 0.9 allows one miss — enough slack for classifier wording noise, not enough to
# let a broken refusal ship.
SCOPE_ADHERENCE_THRESHOLD = 0.9


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


def is_agent_arm(model_name: str, model_summary: Mapping[str, Any]) -> bool:
    """True for an agent-arm row in a pipeline-vs-agent comparison.

    The thresholds were calibrated on the fixed pipeline; the agent arm is
    reported next to it, not gated by it, until its own baseline is measured
    (TASK-5.7).
    """
    return model_summary.get("mode") == "agent" or model_name.endswith(" (agent)")


def check_generation_thresholds(
    generation: Mapping[str, Any] | None,
    thresholds: Mapping[str, float] | None = None,
    *,
    include_agent: bool = False,
) -> list[str]:
    """Return one message per gated dimension that is missing or below threshold.

    An empty list means the gate passed. ``generation`` is the dict returned by
    ``run_generation_eval`` (``{"per_model": {model: {...}}}``). Agent-arm rows
    are skipped unless ``include_agent`` is set.
    """
    thresholds = GENERATION_THRESHOLDS if thresholds is None else thresholds
    per_model = (generation or {}).get("per_model", {}) or {}
    if not per_model:
        return ["no generation scores were produced (is the sample empty?)"]

    failures: list[str] = []
    gated_any = False
    for model_name, model_summary in per_model.items():
        if not include_agent and is_agent_arm(model_name, model_summary):
            continue
        gated_any = True
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
    if not gated_any:
        return [
            "no pipeline-generation scores were produced — the gate is calibrated "
            "on the pipeline arm; re-run with --mode pipeline or --mode both"
        ]
    return failures


def check_claim_violations(
    generation: Mapping[str, Any] | None,
) -> list[str]:
    """Return one message per deterministic claim check that fired.

    These are not score thresholds and need no calibration, so they are gated
    for *every* arm in the run: a list example that contradicts Pd's inlet order,
    a claimed object behaviour no retrieved excerpt covers, or a URL that is not
    in the excerpts is wrong in either arm, and it is deterministic — repeating
    the run does not make it go away the way judge noise does.

    Detail comes from the per-entry records (one per entry per trial); the
    per-model counts are the fallback for a summary that carries no entries.

    Only ``error``-severity violations fail the gate. A ``warning`` (a true claim
    the retrieved excerpts do not state) is reported in the eval output, because
    gating on it would turn a one-in-three model habit into a flaky build.
    """
    generation = generation or {}
    entries = generation.get("entries") or []
    failures: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for label, items in claims.violations_by_arm(entries).items():
        for item in items:
            if item.get("severity") == claims.WARNING:
                continue
            key = (label, str(item.get("entry_id")), str(item.get("check")))
            if key in seen:
                continue
            seen.add(key)
            failures.append(f"{label}: {claims.describe(item)}")
    if entries:
        return failures

    for label, model_summary in (generation.get("per_model") or {}).items():
        count = model_summary.get(
            "claim_violation_count", model_summary.get("claim_violation_count_avg")
        )
        if isinstance(count, (int, float)) and count:
            failures.append(
                f"{label}: {count:g} deterministic claim check(s) flagged"
            )
    return failures


def check_scope_threshold(
    scope_result: Mapping[str, Any] | None,
    threshold: float = SCOPE_ADHERENCE_THRESHOLD,
) -> list[str]:
    """Return a failure message when the scope gate misses too many cases."""
    if not scope_result or not scope_result.get("n"):
        return ["scope eval did not run (expected alongside the generation eval)"]
    adherence = scope_result.get("adherence", 0.0)
    if adherence < threshold:
        misses = [
            f"{c['id']}: expected {c['expected']}, got {c['predicted']}"
            for c in (scope_result.get("failures") or [])[:5]
        ]
        detail = "; ".join(misses) if misses else "no failure detail"
        return [f"scope adherence {adherence:.2f} < {threshold:.2f} ({detail})"]
    return []


def _format_cost(model_summary: Mapping[str, Any]) -> str:
    for key in ("avg_total_cost_usd", "avg_total_cost_usd_avg", "avg_cost_usd"):
        value = model_summary.get(key)
        if isinstance(value, (int, float)):
            return f"${value:.4f}"
    return "n/a"


def _number(model_summary: Mapping[str, Any], *keys: str) -> float:
    """First numeric value under a single-trial key or its multi-trial ``_avg``."""
    for key in keys:
        for name in (key, f"{key}_avg"):
            value = model_summary.get(name)
            if isinstance(value, (int, float)):
                return float(value)
    return 0.0


def _latency_cell(model_summary: Mapping[str, Any], key: str) -> str:
    value = _number(model_summary, key)
    return f"{value / 1000:.1f}s" if value else "-"


def _format_score(value: Any) -> str:
    return f"{value:.2f}" if isinstance(value, (int, float)) else "-"


def generation_summary_markdown(
    results: Mapping[str, Any],
    *,
    sample_size: int,
    model: str = CI_MODEL,
    judge_model: str = CI_JUDGE_MODEL,
    thresholds: Mapping[str, float] | None = None,
    include_agent: bool = False,
) -> str:
    """Build the job-summary markdown for a sampled generation run.

    Writes into ``$GITHUB_STEP_SUMMARY`` in CI, so it states the cost bound
    (sample size, models, measured dollars) next to the scores and the verdict.
    """
    thresholds = GENERATION_THRESHOLDS if thresholds is None else thresholds
    generation = results.get("generation", {}) or {}
    per_model = generation.get("per_model", {}) or {}
    scope_result = results.get("scope") or {}
    trials = generation.get("trials") or 1
    # No scores at all is a failure: a run that produced nothing must not pass.
    failures = check_generation_thresholds(
        generation, thresholds, include_agent=include_agent
    )
    claim_failures = check_claim_violations(generation)

    lines: list[str] = [
        "## Generation eval (sampled)",
        "",
        f"- **Generation model:** `{model}`",
        f"- **Judge model:** `{judge_model}` (1-5 rubric)",
        f"- **Sample:** {sample_size} golden entries (deterministic prefix)",
        f"- **Trials:** {trials}" + (" (cells are means across trials)" if trials > 1 else ""),
        f"- **Gate:** "
        + ", ".join(f"{dim} >= {minimum:g}" for dim, minimum in thresholds.items()),
        "",
    ]

    if per_model:
        lines += [
            "| Model | Faithfulness | Relevance | Citation | n | Cost/query "
            "| p50 | p95 |",
            "|-------|--------------|-----------|----------|---|------------|"
            "-----|-----|",
        ]
        for model_name, m in per_model.items():
            lines.append(
                f"| {model_name} "
                f"| {_format_score(mean_score(m, 'faithfulness'))} "
                f"| {_format_score(mean_score(m, 'relevance'))} "
                f"| {_format_score(mean_score(m, 'citation_correctness'))} "
                f"| {m.get('n', 0)} "
                f"| {_format_cost(m)} "
                f"| {_latency_cell(m, 'p50_latency_ms')} "
                f"| {_latency_cell(m, 'p95_latency_ms')} |"
            )
        lines.append("")

        run_cost = sum(
            _number(m, "run_total_cost_usd") for m in per_model.values()
        ) * trials
        if run_cost:
            suffix = f" across {trials} trials" if trials > 1 else ""
            lines.append(
                f"Measured cost for this run: **${run_cost:.4f}**{suffix} "
                "(generation + judge tokens; retrieval calls are extra)."
            )
            lines.append("")

    if scope_result.get("n"):
        lines += [
            "### Scope gate",
            "",
            f"- **Adherence:** {scope_result['adherence']:.0%} of "
            f"{scope_result['n']} cases (threshold {SCOPE_ADHERENCE_THRESHOLD:.0%})",
        ]
        if scope_result.get("failures"):
            lines += [f"- miss: {f['id']} (expected {f['expected']}, "
                      f"got {f['predicted']})" for f in scope_result["failures"]]
        lines.append("")

    if failures or claim_failures:
        lines.append("### Gate: FAILED")
        lines.append("")
        lines += [f"- {message}" for message in failures]
        lines += [f"- claim check: {message}" for message in claim_failures]
    else:
        lines.append("### Gate: passed")
        if per_model:
            lines.append("")
            for model_name, m in per_model.items():
                if is_agent_arm(model_name, m):
                    continue
                for dimension, minimum in thresholds.items():
                    lines.append(
                        f"- {model_name}: {dimension} "
                        f"{_format_score(mean_score(m, dimension))} "
                        f">= {minimum:g}"
                    )
            lines.append("- deterministic claim checks: 0 flagged (all arms)")
    lines.append("")
    return "\n".join(lines)
