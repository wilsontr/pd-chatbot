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

import json
from pathlib import Path
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

# Agent-level thresholds calibrated against the 30-entry x 3-trial baseline
# (TASK-5.21, 2026-11-24: faithfulness 4.66 ± 0.05, relevance 4.87 ± 0.00,
# citation 4.50 ± 0.12, cost/query $0.0125, p50 5.7s, p95 7.8s, tool calls 1.48)
# and the 10-entry CI prefix (2026-11-25: task_success 70%, tool calls 1.40,
# cost/query $0.0090, p95 6.0s; the 10-entry prefix includes known weak entries
# q_006 and q_007, so a 60% floor leaves a 1-entry judge-noise margin).
# Each bound catches real regressions (task-failure spikes, runaway tool loops,
# cost/latency blowups, or broken error recovery) without flaking CI.
#
# These bounds are for the *docs* mode: documentation questions answered without
# patch execution, which is what ``evaluation.evaluate --gate-agent`` measures
# (evaluation/generation.py always answers with execution=False).
AGENT_DOCS_THRESHOLDS: dict[str, float] = {
    "task_success_rate": 0.60,
    "max_avg_tool_calls": 3.5,
    "max_avg_cost_usd": 0.03,
    "max_p95_latency_ms": 20000.0,
    "recovery_rate": 0.50,
}

# Agent bounds for the *execution* mode (TASK-5.36): patch tasks answered with
# run_patch / analyze_audio available, which is what ``evaluation.patch_eval
# --arm with --gate`` measures. Calibrated against docs/PATCH_EVAL.md (16 tasks x 3
# trials, with execution: pass 92%, by trial 81-100%, tool calls 6.3, p95 25.8 s,
# cost/answer $0.0041). An answer that runs its patch, reads the analysis and
# revises takes 2-3x the calls and time of a docs answer, so the docs bounds would
# fail every healthy run. Task success here is the patch pass rate (runs without a
# Pd error and meets its spec), not a judge score; its floor is
# PATCH_EVAL_PASS_FLOOR below.
PATCH_EVAL_PASS_FLOOR = 0.75
AGENT_EXECUTION_THRESHOLDS: dict[str, float] = {
    # The worst single trial so far was 13/16 (81%); one trial in CI, so the floor
    # sits one task below that: 12/16.
    "task_success_rate": PATCH_EVAL_PASS_FLOOR,
    # 6.3 measured; a runaway repair loop shows up as well over 10.
    "max_avg_tool_calls": 10.0,
    # $0.0041 measured; p_14 alone has cost $0.0124 before its skill.
    "max_avg_cost_usd": 0.03,
    # 25.8 s measured over 48 answers, but 43.9 s in a 16-answer CI-shaped run
    # (2026-11-28, local Docker): with one trial the p95 is the slowest answer,
    # so the bound sits well above both.
    "max_p95_latency_ms": 60000.0,
    "recovery_rate": 0.50,
}

AGENT_THRESHOLDS_BY_MODE: dict[str, dict[str, float]] = {
    "docs": AGENT_DOCS_THRESHOLDS,
    "execution": AGENT_EXECUTION_THRESHOLDS,
}

#: The docs-mode bounds, under the name the generation eval has always used.
AGENT_THRESHOLDS = AGENT_DOCS_THRESHOLDS

# Minimum error-encountering tasks required before recovery_rate fails the gate.
# A 10-entry CI run often has 0-1 tool errors; gating on n=1 would be noisy,
# while reporting still shows recovery_rate whenever tasks_with_errors >= 1.
MIN_RECOVERY_ERROR_TASKS = 2


# --- Per-skill gate (TASK-5.15) ---------------------------------------------

# A skill version is activated only when its own slice clears the generation
# judge floors *and* the skill is actually selected from its description. The
# load-rate floor is the skill-specific part: the golden questions are written to
# trigger the skill, so a slice where the model never calls `load_skill` says the
# description is not doing its job, however good the answers look.
SKILL_THRESHOLDS: dict[str, float] = {
    **GENERATION_THRESHOLDS,
    "min_load_rate": 0.5,
}


def format_skill_thresholds(thresholds: Mapping[str, float] | None = None) -> str:
    """Human-readable summary of the per-skill gate thresholds."""
    t = SKILL_THRESHOLDS if thresholds is None else thresholds
    parts = [
        f"{dimension} >= {minimum:g}"
        for dimension, minimum in GENERATION_THRESHOLDS.items()
    ]
    if "min_load_rate" in t:
        parts.append(f"load rate >= {t['min_load_rate']:.0%}")
    return ", ".join(parts)


def check_skill_thresholds(
    result: Mapping[str, Any],
    thresholds: Mapping[str, float] | None = None,
) -> list[str]:
    """Return one message per threshold a skill slice misses.

    ``result`` is one ``evaluation.skills.run_skill_eval`` summary. A slice with
    no judged answer cannot have passed, so it fails without looking at means.
    """
    t = SKILL_THRESHOLDS if thresholds is None else thresholds
    name = str(result.get("skill") or "skill")
    if int(result.get("judged") or 0) <= 0:
        return [f"{name}: no answer in the slice could be judged"]

    failures: list[str] = []
    for dimension in GENERATION_THRESHOLDS:
        mean = result.get(f"{dimension}_mean")
        minimum = t.get(dimension, GENERATION_THRESHOLDS[dimension])
        if not isinstance(mean, (int, float)):
            failures.append(f"{name}: {dimension} has no usable score")
        elif mean < minimum:
            failures.append(f"{name}: {dimension} {mean:.2f} < {minimum:g}")

    minimum_load = t.get("min_load_rate")
    if minimum_load is not None:
        load_rate = result.get("load_rate")
        if not isinstance(load_rate, (int, float)):
            failures.append(f"{name}: load rate is missing")
        elif load_rate < minimum_load:
            failures.append(
                f"{name}: skill loaded for {load_rate:.0%} of its slice "
                f"< {minimum_load:.0%}"
            )
    return failures


def skills_summary_markdown(skills_root: str | Path | None = None) -> str:
    """Per-skill section for the evaluation report, from the activation manifest.

    Returns an empty string when the repository has no manifest (a development
    checkout before the gate was introduced). Kept dependency-free so the report
    builder and its tests do not need the eval harness.
    """
    root = (
        Path(skills_root)
        if skills_root is not None
        else Path(__file__).resolve().parents[1] / "skills"
    )
    path = root / "activation.json"
    if not path.is_file():
        return ""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    skills = raw.get("skills") if isinstance(raw, dict) else None
    if not isinstance(skills, dict) or not skills:
        return ""

    def metrics_cell(metrics: Mapping[str, Any], key: str) -> str:
        value = metrics.get(key)
        return f"{value:.2f}" if isinstance(value, (int, float)) else "—"

    lines = [
        "## Skills (eval-gated)",
        "",
        "Each skill version is served only after its own golden slice passes the "
        "gate in CI; the activated version and its measured evidence live in "
        "[`skills/activation.json`](../skills/activation.json). Per-question results "
        "and the current thresholds are in [SKILLS.md](SKILLS.md).",
        "",
        "| Skill | Activated version | Faithfulness | Relevance | Evidence | Activated |",
        "|---|---|---|---|---|---|",
    ]
    for name in sorted(skills):
        entry = skills[name] if isinstance(skills[name], dict) else {}
        metrics = entry.get("metrics") if isinstance(entry.get("metrics"), dict) else {}
        evidence = "baseline" if metrics.get("baseline") else "gated"
        when = " · ".join(
            str(bit) for bit in (entry.get("activated_at"), entry.get("activated_by")) if bit
        )
        lines.append(
            "| {name} | `{version}` | {faith} | {relevance} | {evidence} | {when} |".format(
                name=name,
                version=entry.get("version", ""),
                faith=metrics_cell(metrics, "faithfulness_mean"),
                relevance=metrics_cell(metrics, "relevance_mean"),
                evidence=evidence,
                when=when or "—",
            )
        )
    lines.append("")
    return "\n".join(lines)


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

    ``check_generation_thresholds`` gates pipeline arms by default and includes
    agent arms when ``include_agent=True`` (enabled in CI via ``--gate-agent``);
    ``check_agent_thresholds`` enforces the agent-specific task metrics
    calibrated in TASK-5.7.
    """
    return model_summary.get("mode") == "agent" or model_name.endswith(" (agent)")


def _optional_number(model_summary: Mapping[str, Any], *keys: str) -> float | None:
    """First numeric value under a single-trial key or its multi-trial ``_avg``."""
    for key in keys:
        for name in (key, f"{key}_avg"):
            value = model_summary.get(name)
            if isinstance(value, (int, float)):
                return float(value)
    return None


def recovery_stats(model_summary: Mapping[str, Any]) -> tuple[float | None, int, int]:
    """Return ``(recovery_rate, tasks_recovered, tasks_with_errors)``.

    Pools counts across trials when ``tasks_with_errors_by_trial`` is present so
    multi-trial recovery is exact rather than an unweighted average of trial rates.
    """
    errors_by_trial = model_summary.get("tasks_with_errors_by_trial")
    if isinstance(errors_by_trial, list):
        errors = int(sum(v for v in errors_by_trial if isinstance(v, (int, float))))
        rec_by_trial = model_summary.get("tasks_recovered_by_trial")
        recovered = (
            int(sum(v for v in rec_by_trial if isinstance(v, (int, float))))
            if isinstance(rec_by_trial, list)
            else 0
        )
        return ((recovered / errors if errors else None), recovered, errors)

    raw_errors = model_summary.get("tasks_with_errors")
    if isinstance(raw_errors, (int, float)):
        errors = int(raw_errors)
        if errors <= 0:
            return (None, 0, 0)
        raw_recovered = model_summary.get("tasks_recovered")
        if isinstance(raw_recovered, (int, float)):
            recovered = int(raw_recovered)
            return (recovered / errors, recovered, errors)
        rate = _optional_number(model_summary, "recovery_rate")
        if rate is not None:
            return (rate, round(rate * errors), errors)
        return (None, 0, errors)

    rate = _optional_number(model_summary, "recovery_rate")
    return (rate, 0, 0)


def format_agent_thresholds(
    thresholds: Mapping[str, float] | None = None,
    *,
    min_error_tasks: int = MIN_RECOVERY_ERROR_TASKS,
) -> str:
    """Human-readable summary of the agent-level gate thresholds."""
    t = AGENT_THRESHOLDS if thresholds is None else thresholds
    parts: list[str] = []
    if "task_success_rate" in t:
        parts.append(f"task_success >= {t['task_success_rate']:.0%}")
    if "max_avg_tool_calls" in t:
        parts.append(f"tool_calls <= {t['max_avg_tool_calls']:g}")
    if "max_avg_cost_usd" in t:
        parts.append(f"cost/task <= ${t['max_avg_cost_usd']:.4f}")
    if "max_p95_latency_ms" in t:
        parts.append(f"p95 <= {t['max_p95_latency_ms'] / 1000:.1f}s")
    if "recovery_rate" in t:
        parts.append(
            f"recovery >= {t['recovery_rate']:.0%} (when >= {min_error_tasks} error tasks)"
        )
    return ", ".join(parts)


def check_agent_thresholds(
    generation: Mapping[str, Any] | None,
    thresholds: Mapping[str, float] | None = None,
    *,
    min_error_tasks: int = MIN_RECOVERY_ERROR_TASKS,
) -> list[str]:
    """Return one message per agent metric that breaches its calibrated bound.

    Checks task success rate, tool calls per task, cost per task, p95 latency,
    and recovery rate (when enough tasks encountered tool errors).
    """
    t = AGENT_THRESHOLDS if thresholds is None else thresholds
    per_model = (generation or {}).get("per_model", {}) or {}
    agent_rows = [
        (name, summary)
        for name, summary in per_model.items()
        if is_agent_arm(name, summary)
    ]
    if not agent_rows:
        return [
            "no agent-arm scores were produced — re-run with --mode agent or --mode both"
        ]

    failures: list[str] = []
    for model_name, model_summary in agent_rows:
        attempts = _optional_number(model_summary, "task_attempts")
        if not model_summary.get("n") and not attempts:
            failures.append(f"{model_name}: no entries were scored")
            continue

        if "task_success_rate" in t:
            rate = _optional_number(model_summary, "task_success_rate")
            minimum = t["task_success_rate"]
            if rate is None:
                failures.append(f"{model_name}: task_success_rate missing")
            elif rate < minimum:
                failures.append(
                    f"{model_name}: task_success_rate {rate:.2f} < {minimum:.2f} threshold"
                )

        if "max_avg_tool_calls" in t:
            calls = _optional_number(model_summary, "avg_tool_calls")
            maximum = t["max_avg_tool_calls"]
            if calls is not None and calls > maximum:
                failures.append(
                    f"{model_name}: avg_tool_calls {calls:.2f} > {maximum:.2f} threshold"
                )

        if "max_avg_cost_usd" in t:
            cost = _optional_number(model_summary, "avg_total_cost_usd", "avg_cost_usd")
            maximum = t["max_avg_cost_usd"]
            if cost is not None and cost > maximum:
                failures.append(
                    f"{model_name}: avg_cost_usd ${cost:.4f} > ${maximum:.4f} threshold"
                )

        if "max_p95_latency_ms" in t:
            p95 = _optional_number(model_summary, "p95_latency_ms")
            maximum = t["max_p95_latency_ms"]
            if p95 is not None and p95 > maximum:
                failures.append(
                    f"{model_name}: p95_latency {p95 / 1000:.1f}s > {maximum / 1000:.1f}s threshold"
                )

        if "recovery_rate" in t:
            rate, recovered, errors = recovery_stats(model_summary)
            minimum = t["recovery_rate"]
            if errors >= min_error_tasks:
                if rate is None:
                    failures.append(f"{model_name}: recovery_rate missing")
                elif rate < minimum:
                    failures.append(
                        f"{model_name}: recovery_rate {rate:.2f} ({recovered}/{errors}) "
                        f"< {minimum:.2f} threshold"
                    )

    return failures


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


def _format_rate(value: float | None) -> str:
    return f"{value:.0%}" if value is not None else "-"


def _format_recovery(model_name: str, model_summary: Mapping[str, Any]) -> str:
    if not is_agent_arm(model_name, model_summary):
        return "-"
    rate, recovered, errors = recovery_stats(model_summary)
    if errors > 0 and rate is not None:
        return f"{rate:.0%} ({recovered}/{errors})"
    if rate is not None:
        return f"{rate:.0%}"
    return "n/a (0 errors)"


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
    if include_agent:
        failures += check_agent_thresholds(generation)
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
    ]
    if include_agent:
        lines.append(f"- **Agent gate:** {format_agent_thresholds()}")
    lines.append("")

    if per_model:
        has_agent_metrics = any(
            is_agent_arm(name, m) or _optional_number(m, "task_success_rate") is not None
            for name, m in per_model.items()
        )
        header = (
            "| Model | Faithfulness | Relevance | Citation | n | Cost/query "
            "| p50 | p95 |"
        )
        divider = (
            "|-------|--------------|-----------|----------|---|------------|"
            "-----|-----|"
        )
        if has_agent_metrics:
            header += " Task success | Tool calls | Recovery |"
            divider += "--------------|------------|----------|"
        lines += [header, divider]
        for model_name, m in per_model.items():
            row = (
                f"| {model_name} "
                f"| {_format_score(mean_score(m, 'faithfulness'))} "
                f"| {_format_score(mean_score(m, 'relevance'))} "
                f"| {_format_score(mean_score(m, 'citation_correctness'))} "
                f"| {m.get('n', 0)} "
                f"| {_format_cost(m)} "
                f"| {_latency_cell(m, 'p50_latency_ms')} "
                f"| {_latency_cell(m, 'p95_latency_ms')} |"
            )
            if has_agent_metrics:
                calls = _optional_number(m, "avg_tool_calls")
                calls_cell = (
                    "0"
                    if calls is None
                    else (str(int(calls)) if float(calls).is_integer() else f"{calls:.2f}")
                )
                row += (
                    f" {_format_rate(_optional_number(m, 'task_success_rate'))} "
                    f"| {calls_cell} "
                    f"| {_format_recovery(model_name, m)} |"
                )
            lines.append(row)
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
                if not include_agent and is_agent_arm(model_name, m):
                    continue
                for dimension, minimum in thresholds.items():
                    lines.append(
                        f"- {model_name}: {dimension} "
                        f"{_format_score(mean_score(m, dimension))} "
                        f">= {minimum:g}"
                    )
                if include_agent and is_agent_arm(model_name, m):
                    rate = _optional_number(m, "task_success_rate")
                    if rate is not None:
                        lines.append(
                            f"- {model_name}: task_success_rate {rate:.0%} "
                            f">= {AGENT_THRESHOLDS['task_success_rate']:.0%}"
                        )
            lines.append("- deterministic claim checks: 0 flagged (all arms)")
    lines.append("")
    return "\n".join(lines)
