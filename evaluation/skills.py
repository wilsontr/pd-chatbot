"""Per-skill evaluation, gating and activation (TASK-5.15).

A skill version becomes live only after its own golden slice passes here. The
commands mirror the generation gate:

    # Run the slices for the skills a change touched, gate them, and activate
    # the versions that pass (what CI does on a pull request).
    python -m evaluation.skills --changed-from origin/main --gate --activate \\
        --report docs/SKILLS.md --summary "$GITHUB_STEP_SUMMARY"

    # Run every slice, gate, don't activate (a local check before opening a PR).
    python -m evaluation.skills --all --gate

    # Rewrite docs/SKILLS.md from the activation manifest, no API calls.
    python -m evaluation.skills --render-report docs/SKILLS.md

The slice is answered by the real agent with **only the skill under test**
available, so the measurement is attributable to that skill rather than to a
neighbour it happened to load. The answers are scored with the same judge and
deterministic claim checks as the generation eval, and the gate reuses those
floors plus a load-rate floor: a skill that is never selected from its own
description is not improving anything.

Activation copies the passing version into ``skills/.activated/`` and pins it in
``skills/activation.json`` (see ``agent.skills.activation``). A version that
fails leaves the manifest untouched, so the previously activated snapshot keeps
serving — that is how "the previous version stays live" is enforced.

``--baseline`` is the one path that activates without a measurement. It exists
so the gate could be introduced without a paid run for every existing skill; CI
never calls it, and the manifest records the versions as pre-gate.

Evidence and the check on main (TASK-5.37). An activation made inside GitHub
Actions records the run (``metrics.ci``: run id and URL, workflow, commit).
``--verify`` reads the manifest, makes no API calls, and fails when a pinned
version has no CI evidence (a baseline or a local activation) or its snapshot
is missing or does not hash to the pinned digest. It runs on every push to main
and on pull requests (``.github/workflows/skills-check.yml``). A skill that
cannot pass its gate is taken out of service with ``--deactivate NAME --reason``,
which removes its pin and records why::

    python -m evaluation.skills --verify
    python -m evaluation.skills --deactivate pd-flavors --reason "fails relevance"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from model_config import ANSWER_MODEL, JUDGE_MODEL
import pricing

from .gates import (
    SKILL_THRESHOLDS,
    check_skill_thresholds,
    format_skill_thresholds,
)
from agent.skills import Skill, SkillRegistry
from agent.skills.activation import (
    ActivationEntry,
    ActivationError,
    load_deactivated,
    load_manifest,
    prune_snapshots,
    store_snapshot,
    skill_digest,
    snapshot_path,
    write_manifest,
)
from agent.skills.golden import read_golden_set

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = REPO_ROOT / "skills"

#: A slice entry with no retrieved context cannot be scored; it counts against
#: the load/task rates (the agent failed to gather evidence) but not the means.
STATUS_JUDGED = "judged"
STATUS_NO_CONTEXT = "no_context"
STATUS_ERROR = "error"

AnswerFn = Callable[[str, list[dict[str, str]]], Any]
JudgeFn = Callable[
    [str, str, list[dict[str, Any]], dict[str, dict[str, int]]],
    dict[str, Any],
]


# --- The real agent and judge -------------------------------------------------


def _normalise_usage(usage: Mapping[str, int] | None) -> dict[str, int]:
    raw = usage or {}
    return {
        "input_tokens": int(raw.get("input_tokens", raw.get("input", 0)) or 0),
        "output_tokens": int(raw.get("output_tokens", raw.get("output", 0)) or 0),
        "cache_read_tokens": int(
            raw.get("cache_read_tokens", raw.get("cache_read", 0)) or 0
        ),
    }


def _build_answer_fn(skill_name: str, root: Path, model: str) -> AnswerFn:
    """The shipped agent, with only the skill under test installed."""
    from agent.chat import answer_question, default_config, default_registry
    from agent.workspace import PatchWorkspace

    registry = SkillRegistry(root, activation=False, only={skill_name})
    if registry.get(skill_name) is None:
        raise ValueError(f"no skill named {skill_name!r} under {root}")
    config = default_config(execution=False, model=model, interface="eval", skills=True)

    def answer(question: str, history: list[dict[str, str]]) -> Any:
        # A fresh workspace per question keeps one entry's patches out of the
        # next; it is handed to both the tools and answer_question so the
        # patches a skill's answer writes are the ones that come back.
        workspace = PatchWorkspace()
        tools = default_registry(
            workspace, execution=False, interface="eval", skills=registry
        )
        return asyncio.run(
            answer_question(
                question,
                history,
                tools=tools,
                workspace=workspace,
                config=config,
                execution=False,
                interface="eval",
            )
        )

    return answer


def _build_judge_fn(judge_model: str) -> JudgeFn:
    import anthropic

    from .judge import evaluate_generation

    client = anthropic.Anthropic()

    def judge(
        question: str,
        answer: str,
        chunks: list[dict[str, Any]],
        usage_sink: dict[str, dict[str, int]],
    ) -> dict[str, Any]:
        return evaluate_generation(
            client,
            question=question,
            answer=answer,
            context_chunks=chunks,
            judge_model=judge_model,
            usage_sink=usage_sink,
        )

    return judge


# --- One skill's slice --------------------------------------------------------


def run_skill_eval(
    skill_name: str,
    *,
    root: str | Path = SKILLS_ROOT,
    model: str = ANSWER_MODEL,
    judge_model: str = JUDGE_MODEL,
    answer_fn: AnswerFn | None = None,
    judge_fn: JudgeFn | None = None,
    verbose: bool = False,
    trials: int = 1,
) -> dict[str, Any]:
    """Answer and score one skill's golden slice; return its result dict.

    With ``trials`` > 1 every question is answered that many times and the gate
    reads the pooled answers (TASK-5.40): one judged answer's score moves by
    about a point between runs, so a 15-question slice needs three trials for its
    means to settle within ~0.15.
    """
    if trials < 1:
        raise ValueError("trials must be at least 1")
    from .generation import _is_task_success  # shares the generation eval's pass rule
    from . import claims

    root_path = Path(root).expanduser()
    registry = SkillRegistry(root_path, activation=False, only={skill_name})
    skill = registry.get(skill_name)
    if skill is None:
        raise ValueError(f"no skill named {skill_name!r} under {root_path}")
    golden = read_golden_set(skill)
    if golden is None:
        raise ValueError(f"skill {skill_name!r} has no golden set to evaluate")

    answer_fn = answer_fn or _build_answer_fn(skill_name, root_path, model)
    judge_fn = judge_fn or _build_judge_fn(judge_model)
    judge_usage: dict[str, dict[str, int]] = {}

    entries: list[dict[str, Any]] = []
    runs = [(trial, q) for trial in range(1, trials + 1) for q in golden.questions]
    for trial, question in runs:
        entry: dict[str, Any] = {
            "id": question.id,
            "question": question.question,
            "trial": trial,
        }
        started = time.perf_counter()
        try:
            result = answer_fn(question.question, [])
        except Exception as exc:  # a failed run must not hide the rest of the slice
            logger.exception("skill slice entry %s failed", question.id)
            entry.update(
                {
                    "status": STATUS_ERROR,
                    "error": str(exc),
                    "loaded_skill": False,
                    "judged": False,
                    "task_success": False,
                    "tool_calls": 0,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                }
            )
            entries.append(entry)
            continue

        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        answer = str(getattr(result, "answer", "") or "")
        chunks = list(getattr(result, "chunks", []) or [])
        loaded = skill_name in dict(getattr(result, "loaded_skills", {}) or {})
        usage = _normalise_usage(getattr(result, "usage", None))
        tool_calls = int(getattr(result, "tool_calls", 0) or 0)
        entry.update(
            {
                "answer": answer,
                "loaded_skill": loaded,
                "tool_calls": tool_calls,
                "usage": usage,
                "latency_ms": latency_ms,
                "chunks_retrieved": len(chunks),
            }
        )

        if not chunks:
            # Nothing was retrieved, so there is no evidence to judge against —
            # but the entry still counts as an attempt (and a miss for the gate).
            entry.update(
                {
                    "status": STATUS_NO_CONTEXT,
                    "judged": False,
                    "task_success": False,
                    "scores": {},
                    "claim_violations": [],
                }
            )
        else:
            scores = judge_fn(question.question, answer, chunks, judge_usage)
            violations = [
                violation.as_dict()
                for violation in claims.check_answer(question.id, answer, chunks)
            ]
            entry.update(
                {
                    "status": STATUS_JUDGED,
                    "judged": True,
                    "scores": scores,
                    "claim_violations": violations,
                    "task_success": _is_task_success(scores, violations),
                }
            )

        cost = pricing.cost_usd(model, usage)
        entry["cost_usd"] = round(cost, 6) if cost is not None else None
        if verbose:
            verdict = "success" if entry["task_success"] else entry["status"]
            tag = f" t{trial}" if trials > 1 else ""
            print(f"  {question.id}{tag}: {verdict} (loaded={loaded}, {latency_ms:.0f} ms)")
        entries.append(entry)

    summary = _summarise(skill, entries, judge_usage, model, judge_model)
    summary["trials"] = trials
    summary["questions"] = len(golden.questions)
    summary["answers"] = len(entries)
    if trials > 1:
        summary["faithfulness_by_trial"] = [
            _mean_score([e for e in entries if e.get("trial") == t], "faithfulness")
            for t in range(1, trials + 1)
        ]
    if verbose:
        print(
            f"{skill.name} v{skill.version}: judged {summary['judged']}/"
            f"{summary['answers']} ({summary['questions']} questions x {trials}), faithfulness {summary['faithfulness_mean']}, "
            f"relevance {summary['relevance_mean']}, load rate "
            f"{summary['load_rate']:.0%}"
        )
    return summary


def _mean_score(entries: list[dict[str, Any]], dimension: str) -> float | None:
    scores = [
        (entry.get("scores", {}).get(dimension) or {}).get("score")
        for entry in entries if entry.get("judged")
    ]
    scores = [score for score in scores if isinstance(score, (int, float))]
    return round(sum(scores) / len(scores), 2) if scores else None


def _summarise(
    skill: Skill,
    entries: list[dict[str, Any]],
    judge_usage: dict[str, dict[str, int]],
    model: str,
    judge_model: str,
) -> dict[str, Any]:
    judged = [entry for entry in entries if entry.get("judged")]
    summary: dict[str, Any] = {
        "skill": skill.name,
        "version": skill.version,
        "digest": skill.digest,
        "path": str(skill.path),
        "questions": len(entries),
        "judged": len(judged),
        "run_errors": sum(1 for entry in entries if entry["status"] == STATUS_ERROR),
        "skipped_no_context": sum(
            1 for entry in entries if entry["status"] == STATUS_NO_CONTEXT
        ),
        "model": model,
        "judge_model": judge_model,
        "entries": entries,
    }

    for dimension in ("faithfulness", "relevance", "citation_correctness"):
        scores = [
            (entry["scores"].get(dimension) or {}).get("score")
            for entry in judged
        ]
        scores = [score for score in scores if isinstance(score, (int, float))]
        summary[f"{dimension}_mean"] = (
            round(sum(scores) / len(scores), 2) if scores else None
        )
        summary[f"{dimension}_dropped"] = len(judged) - len(scores)

    successes = sum(1 for entry in entries if entry.get("task_success"))
    summary["task_successes"] = successes
    summary["task_success_rate"] = (
        round(successes / len(entries), 4) if entries else 0.0
    )
    loads = sum(1 for entry in entries if entry.get("loaded_skill"))
    summary["skill_loads"] = loads
    summary["load_rate"] = round(loads / len(entries), 4) if entries else 0.0

    costs = [entry["cost_usd"] for entry in entries if entry.get("cost_usd") is not None]
    if costs:
        summary["avg_cost_usd"] = round(sum(costs) / len(costs), 6)
        summary["run_cost_usd"] = round(sum(costs), 6)
    judge_costs = [
        pricing.cost_usd(judge_model, usage) for usage in judge_usage.values()
    ]
    judge_costs = [cost for cost in judge_costs if cost is not None]
    if judge_costs:
        summary["judge_cost_usd"] = round(sum(judge_costs), 6)
        summary["run_total_cost_usd"] = round(
            sum(costs) + sum(judge_costs), 6
        )

    latencies = [entry["latency_ms"] for entry in entries if "latency_ms" in entry]
    if latencies:
        summary["avg_latency_ms"] = round(sum(latencies) / len(latencies), 1)
        summary["p95_latency_ms"] = round(
            sorted(latencies)[min(len(latencies) - 1, int(0.95 * len(latencies)) - 1)],
            1,
        )
    tool_calls = [entry.get("tool_calls", 0) for entry in entries]
    summary["avg_tool_calls"] = (
        round(sum(tool_calls) / len(tool_calls), 2) if tool_calls else 0.0
    )
    return summary


def gate_and_activate(
    result: Mapping[str, Any],
    *,
    root: str | Path = SKILLS_ROOT,
    activate: bool = False,
    model: str = ANSWER_MODEL,
    judge_model: str = JUDGE_MODEL,
    actor: str = "",
    thresholds: Mapping[str, float] | None = None,
    activated_at: str | None = None,
) -> tuple[list[str], bool]:
    """Gate one result; activate its version only when it passes.

    Returns ``(failures, activated)``.
    """
    failures = check_skill_thresholds(result, thresholds)
    if failures or not activate:
        return failures, False
    activated = activate_result(
        result,
        root=root,
        model=model,
        judge_model=judge_model,
        actor=actor,
        activated_at=activated_at,
    )
    return failures, activated


def _metrics_record(result: Mapping[str, Any]) -> dict[str, Any]:
    """The measured evidence stored in the manifest for this version."""
    return {
        key: result[key]
        for key in (
            "questions",
            "trials",
            "answers",
            "judged",
            "task_success_rate",
            "load_rate",
            "faithfulness_mean",
            "relevance_mean",
            "citation_correctness_mean",
            "avg_cost_usd",
            "avg_latency_ms",
        )
        if result.get(key) is not None
    }


def activate_result(
    result: Mapping[str, Any],
    *,
    root: str | Path = SKILLS_ROOT,
    model: str = ANSWER_MODEL,
    judge_model: str = JUDGE_MODEL,
    actor: str = "",
    activated_at: str | None = None,
) -> bool:
    """Pin one evaluated (and passing) skill version; return whether it changed.

    The snapshot of the version is written before the manifest, so the manifest
    never points at content that is not on disk.
    """
    root_path = Path(root).expanduser()
    name = str(result["skill"])
    version = str(result["version"])
    source = Path(str(result.get("path") or ""))
    if not source.is_dir():
        registry = SkillRegistry(root_path, activation=False, only={name})
        skill = registry.get(name)
        if skill is None or skill.version != version:
            raise ActivationError(f"cannot snapshot {name}: source folder is gone")
        source = skill.path

    digest = str(result.get("digest") or "")
    if not digest:
        digest = skill_digest(source)
    store_snapshot(root_path, name, digest, source)
    entries = load_manifest(root_path) or {}
    previous = entries.get(name)
    entries[name] = ActivationEntry(
        version=version,
        digest=digest,
        activated_at=activated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        activated_by=actor or os.environ.get("GITHUB_ACTOR", "") or "local",
        metrics=_metrics_record(result)
        | {"model": model, "judge_model": judge_model}
        | ({"ci": evidence} if (evidence := ci_evidence()) else {}),
    )
    write_manifest(root_path, entries)
    prune_snapshots(root_path, name)
    changed = previous is None or previous.digest != digest
    logger.info(
        "activated %s %s%s", name, version, "" if changed else " (version unchanged)"
    )
    return changed


#: The workflow whose runs count as gate evidence (``name:`` in skills.yml).
GATE_WORKFLOW = "Skill eval gate"


def ci_evidence(env: Mapping[str, str] | None = None) -> dict[str, str] | None:
    """The GitHub Actions run doing this activation, or None outside CI."""
    env = os.environ if env is None else env
    if env.get("GITHUB_ACTIONS") != "true" or not env.get("GITHUB_RUN_ID"):
        return None
    server = env.get("GITHUB_SERVER_URL", "https://github.com")
    repo = env.get("GITHUB_REPOSITORY", "")
    run_id = env["GITHUB_RUN_ID"]
    return {
        "run_id": run_id,
        "run_url": f"{server}/{repo}/actions/runs/{run_id}",
        "workflow": env.get("GITHUB_WORKFLOW", ""),
        "event": env.get("GITHUB_EVENT_NAME", ""),
        "commit": env.get("GITHUB_SHA", ""),
    }


def deactivate(
    name: str,
    reason: str,
    *,
    root: str | Path = SKILLS_ROOT,
    actor: str = "",
    deactivated_at: str | None = None,
) -> None:
    """Take a skill out of service: drop its pin and record why."""
    if not reason.strip():
        raise ActivationError("a deactivation needs a reason")
    root_path = Path(root).expanduser()
    entries = load_manifest(root_path) or {}
    previous = entries.pop(name, None)
    deactivated = load_deactivated(root_path)
    deactivated[name] = {
        "reason": reason.strip(),
        "deactivated_at": deactivated_at
        or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "deactivated_by": actor or os.environ.get("GITHUB_ACTOR", "") or "local",
        "last_digest": previous.digest if previous else "",
    }
    write_manifest(root_path, entries, deactivated)


def verify_manifest(root: str | Path = SKILLS_ROOT) -> tuple[list[str], list[str]]:
    """Check every pin has CI-gated evidence and a matching snapshot.

    Returns ``(failures, notes)``. Notes are not failures: a working folder that
    differs from its pin serves the pinned snapshot until a gate activates it.
    """
    root_path = Path(root).expanduser()
    entries = load_manifest(root_path)
    if entries is None:
        return [f"no {root_path.name}/activation.json"], []
    failures: list[str] = []
    notes: list[str] = []
    for name, entry in sorted(entries.items()):
        metrics = entry.metrics or {}
        ci = metrics.get("ci") if isinstance(metrics.get("ci"), Mapping) else None
        if metrics.get("baseline"):
            failures.append(f"{name} {entry.version}: pinned as an ungated baseline")
        elif not ci or not ci.get("run_id") or ci.get("workflow") != GATE_WORKFLOW:
            failures.append(
                f"{name} {entry.version}: no CI-gated evidence "
                f"(activated by {entry.activated_by or 'unknown'})"
            )
        try:
            snapshot = snapshot_path(root_path, name, entry.digest)
        except ActivationError as exc:
            failures.append(f"{name}: {exc}")
            continue
        if not snapshot.is_dir():
            failures.append(f"{name} {entry.version}: snapshot {snapshot.name[:12]} is missing")
        elif skill_digest(snapshot) != entry.digest:
            failures.append(
                f"{name} {entry.version}: snapshot does not match its pinned digest"
            )
        folder = root_path / name
        if folder.is_dir() and skill_digest(folder) != entry.digest:
            notes.append(
                f"{name}: working folder differs from the pin; the snapshot serves "
                "until the gate activates the new version"
            )
    for name, record in sorted(load_deactivated(root_path).items()):
        if not str(record.get("reason") or "").strip():
            failures.append(f"{name}: deactivated with no reason")
        else:
            notes.append(f"{name}: deactivated — {record['reason']}")
    return failures, notes


def activate_baseline(
    *,
    root: str | Path = SKILLS_ROOT,
    only: Iterable[str] | None = None,
    actor: str = "baseline",
    reason: str = "pre-gate baseline",
    activated_at: str | None = None,
) -> list[str]:
    """Pin the current version of every skill without measuring it.

    For introducing the gate on an existing skill set; the manifest records the
    versions as pre-gate so a reader can tell an unmeasured baseline from a
    version that earned activation. CI never calls this.
    """
    root_path = Path(root).expanduser()
    registry = SkillRegistry(root_path, activation=False, only=only)
    entries = load_manifest(root_path) or {}
    activated: list[str] = []
    stamp = activated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for skill in registry.skills():
        store_snapshot(root_path, skill.name, skill.digest, skill.path)
        entries[skill.name] = ActivationEntry(
            version=skill.version,
            digest=skill.digest,
            activated_at=stamp,
            activated_by=actor,
            metrics={"baseline": True, "reason": reason},
        )
        activated.append(skill.name)
    write_manifest(root_path, entries)
    return activated


# --- Reporting ----------------------------------------------------------------


def _cell(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _percent(value: Any) -> str:
    return "—" if value is None else f"{float(value):.0%}"


def render_report(
    *,
    root: str | Path = SKILLS_ROOT,
    results: Sequence[Mapping[str, Any]] = (),
    verdicts: Mapping[str, bool] | None = None,
    thresholds: Mapping[str, float] | None = None,
) -> str:
    """Render the per-skill eval report (``docs/SKILLS.md``)."""
    root_path = Path(root).expanduser()
    manifest = load_manifest(root_path) or {}
    measured = {str(result["skill"]): result for result in results}
    verdicts = verdicts or {}
    working = SkillRegistry(root_path, activation=False)

    lines = [
        "# Skill evaluation",
        "",
        "*Auto-generated by `python -m evaluation.skills`. Do not edit by hand.*",
        "",
        "A skill version is served only after its own golden slice passes. The gate "
        "runs on every change under `skills/` (`.github/workflows/skills.yml`); "
        "`skills/activation.json` records the activated version and the measured "
        f"evidence, and `skills/.activated/` keeps the snapshot that serves while a "
        "changed version is ungated.",
        "",
        f"Thresholds: {format_skill_thresholds(thresholds)}.",
        "",
        "| Skill | Version | Activated | Gate | Faithfulness | Relevance | Citation | "
        "Task success | Loaded |",
        "|---|---|---|---|---|---|---|---|---|",
    ]

    rows = sorted(set(working.names()) | set(manifest))
    if not rows:
        lines.append("| _none_ | — | — | — | — | — | — | — | — |")

    for name in rows:
        result = measured.get(name)
        entry = manifest.get(name)
        working_skill = next(
            (skill for skill in working.skills() if skill.name == name), None
        )
        version = (result or {}).get("version") or (
            working_skill.version if working_skill else "—"
        )
        if result:
            passed = verdicts.get(name, not check_skill_thresholds(result, thresholds))
            gate = "pass" if passed else "fail"
            metrics = result
        elif entry is not None:
            metrics = entry.metrics
            gate = "baseline (not measured)" if metrics.get("baseline") else "activated"
        else:
            metrics = {}
            gate = "deactivated" if name in load_deactivated(root_path) else "not activated"
        activated_version = entry.version if entry is not None else "—"
        lines.append(
            "| {name} | `{version}` | `{activated}` | {gate} | {faith} | {relevance} "
            "| {citation} | {success} | {loaded} |".format(
                name=name,
                version=version,
                activated=activated_version,
                gate=gate,
                faith=_cell(metrics.get("faithfulness_mean")),
                relevance=_cell(metrics.get("relevance_mean")),
                citation=_cell(metrics.get("citation_correctness_mean")),
                success=_percent(metrics.get("task_success_rate")),
                loaded=_percent(metrics.get("load_rate")),
            )
        )

    lines += [
        "",
        "Per-question results, including the judge's explanations and any claim "
        "checks, are in the run's JSON output (`--output`) and the CI job summary. "
        "The generation and retrieval eval report is in "
        "[EVAL.md](EVAL.md).",
        "",
    ]
    activated_rows = [
        f"- `{name}` at `{entry.version}`"
        + (f" — activated {entry.activated_at}" if entry.activated_at else "")
        + (f" by {entry.activated_by}" if entry.activated_by else "")
        + (
            f" ([CI run]({entry.metrics['ci']['run_url']}))"
            if isinstance(entry.metrics.get("ci"), Mapping)
            and entry.metrics["ci"].get("run_url")
            else ""
        )
        for name, entry in sorted(manifest.items())
    ]
    if activated_rows:
        lines += ["## Activated versions", "", *activated_rows, ""]
    deactivated = load_deactivated(root_path)
    if deactivated:
        lines += ["## Deactivated", ""] + [
            f"- `{name}` — {record.get('reason', '')}"
            + (f" ({record['deactivated_at']})" if record.get("deactivated_at") else "")
            for name, record in sorted(deactivated.items())
        ] + [""]
    return "\n".join(lines)


def summary_markdown(
    results: Sequence[Mapping[str, Any]],
    verdicts: Mapping[str, bool],
    thresholds: Mapping[str, float] | None = None,
) -> str:
    """The job-summary section for a skill gate run."""
    lines = [
        "### Skill eval gate",
        "",
        f"Thresholds: {format_skill_thresholds(thresholds)}.",
        "",
        "| Skill | Version | Judged (answers = questions x trials) | Faithfulness "
        "| Relevance | Loaded | Verdict |",
        "|---|---|---|---|---|---|---|",
    ]
    for result in results:
        name = str(result["skill"])
        lines.append(
            "| {name} | `{version}` | {judged}/{answers} ({questions} x {trials}) "
            "| {faith} | {relevance} "
            "| {loaded} | {verdict} |".format(
                name=name,
                version=result["version"],
                judged=result.get("judged", 0),
                questions=result.get("questions", 0),
                answers=result.get("answers", result.get("questions", 0)),
                trials=result.get("trials", 1),
                faith=_cell(result.get("faithfulness_mean")),
                relevance=_cell(result.get("relevance_mean")),
                loaded=_percent(result.get("load_rate")),
                verdict="pass" if verdicts.get(name) else "FAIL",
            )
        )
    if not results:
        lines.append("| _no changed skills_ | — | — | — | — | — | skipped |")
    lines.append("")
    return "\n".join(lines)


# --- CLI ----------------------------------------------------------------------


def skills_changed_since(rev: str, root: str | Path = SKILLS_ROOT) -> list[str]:
    """Skill folder names touched between ``rev`` and HEAD."""
    root_path = Path(root).expanduser()
    try:
        completed = subprocess.run(
            ["git", "diff", "--name-only", f"{rev}...HEAD", "--", str(root_path)],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise SystemExit(f"could not diff skills against {rev}: {exc}") from exc
    names: set[str] = set()
    for line in completed.stdout.splitlines():
        parts = Path(line).parts
        if len(parts) < 3 or parts[0] != root_path.name:
            continue
        candidate = parts[1]
        if candidate.startswith(".") or candidate in {"activation.json", "README.md"}:
            continue
        names.add(candidate)
    return sorted(names)


def _selected_skills(args: argparse.Namespace) -> list[str]:
    if args.changed_from:
        return skills_changed_since(args.changed_from, args.skills_root)
    if args.all:
        return SkillRegistry(args.skills_root, activation=False).names()
    if args.skills:
        return [name.strip() for name in args.skills.split(",") if name.strip()]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Per-skill evaluation and activation gate (TASK-5.15)"
    )
    parser.add_argument("--skills", type=str, default=None,
                        help="Comma-separated skill names to evaluate")
    parser.add_argument("--all", action="store_true", help="Evaluate every installed skill")
    parser.add_argument("--changed-from", type=str, default=None, metavar="REV",
                        help="Evaluate the skills changed between REV and HEAD "
                             "(e.g. origin/main in CI)")
    parser.add_argument("--skills-root", type=str, default=str(SKILLS_ROOT),
                        help="Skills directory (default: the repository's skills/)")
    parser.add_argument("--model", type=str, default=ANSWER_MODEL,
                        help=f"Answer model (default: {ANSWER_MODEL})")
    parser.add_argument("--judge", type=str, default=JUDGE_MODEL,
                        help=f"Judge model (default: {JUDGE_MODEL})")
    parser.add_argument("--gate", action="store_true",
                        help="Exit non-zero when a skill's slice misses a threshold")
    parser.add_argument("--activate", action="store_true",
                        help="Pin and snapshot the versions that pass (no-op for failures)")
    parser.add_argument("--baseline", action="store_true",
                        help="Pin the current versions without measuring (bootstraps the "
                             "gate; CI never uses this)")
    parser.add_argument("--baseline-reason", type=str, default="pre-gate baseline",
                        help="Reason recorded in the manifest by --baseline")
    parser.add_argument("--render-report", type=str, default=None, metavar="PATH",
                        help="Write the per-skill report from the manifest, no API calls")
    parser.add_argument("--report", type=str, default=None, metavar="PATH",
                        help="Write the per-skill report after the run (e.g. docs/SKILLS.md)")
    parser.add_argument("--output", type=str, default=None,
                        help="Write the run's JSON results to this path")
    parser.add_argument("--summary", type=str, default=None,
                        help="Append a markdown job summary (e.g. $GITHUB_STEP_SUMMARY)")
    parser.add_argument("--verify", action="store_true",
                        help="Fail when a pinned version lacks CI-gated evidence or its "
                             "snapshot does not match; no API calls (TASK-5.37)")
    parser.add_argument("--deactivate", type=str, default=None, metavar="NAME",
                        help="Stop serving a skill and record --reason in the manifest")
    parser.add_argument("--reason", type=str, default="",
                        help="Why the skill is deactivated (required with --deactivate)")
    parser.add_argument("--trials", type=int, default=1,
                        help="Answer each question this many times and gate on the pooled "
                             "answers (CI uses 3; TASK-5.40)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.skills_root).expanduser()

    if args.verify:
        failures, notes = verify_manifest(root)
        for note in notes:
            print(f"  note: {note}", file=sys.stderr)
        if failures:
            print("Skill activation check FAILED:", file=sys.stderr)
            for failure in failures:
                print(f"  - {failure}", file=sys.stderr)
            return 1
        print("Every pinned skill has CI-gated evidence and a matching snapshot.",
              file=sys.stderr)
        return 0

    if args.deactivate:
        try:
            deactivate(args.deactivate, args.reason, root=root)
        except ActivationError as exc:
            parser.error(str(exc))
        print(f"Deactivated {args.deactivate}: {args.reason}", file=sys.stderr)
        if args.report:
            Path(args.report).write_text(render_report(root=root), encoding="utf-8")
        return 0

    if args.baseline:
        selected = _selected_skills(args) or None
        activated = activate_baseline(
            root=root, only=selected, reason=args.baseline_reason
        )
        print(
            f"Activated {len(activated)} skill(s) as a baseline: "
            + ", ".join(activated),
            file=sys.stderr,
        )
        if args.report:
            Path(args.report).write_text(render_report(root=root), encoding="utf-8")
        return 0

    if args.render_report:
        Path(args.render_report).write_text(render_report(root=root), encoding="utf-8")
        if not args.quiet:
            print(f"Report written to {args.render_report}", file=sys.stderr)
        return 0

    selected = _selected_skills(args)
    if not selected:
        if not args.quiet:
            print("No skills selected; nothing to evaluate.", file=sys.stderr)
        if args.report:
            Path(args.report).write_text(render_report(root=root), encoding="utf-8")
        if args.summary:
            with open(args.summary, "a", encoding="utf-8") as handle:
                handle.write(summary_markdown([], {}))
        return 0

    actor = os.environ.get("GITHUB_ACTOR", "") or "local"
    results: list[dict[str, Any]] = []
    verdicts: dict[str, bool] = {}
    all_failures: list[str] = []
    for name in selected:
        if not args.quiet:
            print(f"\n=== {name} ===\n", file=sys.stderr)
        result = run_skill_eval(
            name,
            root=root,
            model=args.model,
            judge_model=args.judge,
            verbose=not args.quiet,
            trials=args.trials,
        )
        failures, activated = gate_and_activate(
            result,
            root=root,
            activate=args.activate,
            model=args.model,
            judge_model=args.judge,
            actor=actor,
        )
        verdicts[name] = not failures
        all_failures.extend(failures)
        if failures:
            print(f"  GATE FAILED: {'; '.join(failures)}", file=sys.stderr)
        elif args.activate:
            print(
                f"  activated {name} v{result['version']}"
                + ("" if activated else " (already current)"),
                file=sys.stderr,
            )
        results.append(result)

    if args.report:
        Path(args.report).write_text(
            render_report(
                root=root,
                results=results,
                verdicts=verdicts,
                thresholds=SKILL_THRESHOLDS,
            ),
            encoding="utf-8",
        )
        if not args.quiet:
            print(f"Report written to {args.report}", file=sys.stderr)

    if args.output:
        Path(args.output).write_text(
            json.dumps(
                {
                    "thresholds": dict(SKILL_THRESHOLDS),
                    "results": [
                        {key: value for key, value in result.items() if key != "entries"}
                        for result in results
                    ],
                    "entries": [
                        entry for result in results for entry in result.get("entries", [])
                    ],
                    "verdicts": verdicts,
                },
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )

    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as handle:
            handle.write(summary_markdown(results, verdicts, SKILL_THRESHOLDS))

    if args.gate and all_failures:
        print("\nSkill gate FAILED:", file=sys.stderr)
        for failure in all_failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    if args.gate and not args.quiet:
        print("\nSkill gate passed.", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
