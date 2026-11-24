"""Control checks for the faithfulness judge's Pd ordering belief (TASK-5.22).

The judge is an LLM, and an eval that uses it has to be able to show the judge's
beliefs are right before its scores mean anything. This module runs two recorded
``q_004`` answers through the faithfulness rubric:

* a **reversed** list example — it puts the ramp time first and calls the list
  "time, then target", which is the opposite of Pd's left-to-right inlet rule.
  It was scored 5/5/5 before the rubric stated the rule.
* a **correct** left-to-right example — "``500 1000`` ... the first value goes to
  the leftmost inlet". It was scored 2 before the rubric stated the rule.

The bounds are deliberately strict and per-run, not on the mean: a judge that
sometimes rewards the reversal is still wrong about the rule. Both fixtures are
stored with the context they were judged against in
``evaluation/judge_controls.json``, so the control is reproducible without
retrieval.

The scoring path needs an API key, so the module imports and its pure helpers
run offline; only ``run_controls`` calls the judge. ``tests/test_judge_rubric.py``
covers the rubric contract and the bounds without any API calls.
"""

from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from model_config import JUDGE_MODEL

from .judge import score_faithfulness

FIXTURE_PATH = Path(__file__).with_name("judge_controls.json")

#: Extremes a control answer must stay within, keyed by its ``expect`` label.
#: A reversed answer must never be scored above 3 (the rubric's "multiple
#: unflagged claims / one invented specific" band); a correct one must never
#: fall below 4 (the "one minor unflagged claim" band).
REVERSED_MAX_SCORE = 3
CORRECT_MIN_SCORE = 4


@dataclass(frozen=True)
class Control:
    """One recorded answer with the direction the judge must score it in."""

    id: str
    expect: str
    label: str
    answer: str
    recorded_scores: tuple[int, ...] = ()
    recorded_in: str = ""

    @property
    def is_reversed(self) -> bool:
        return self.expect == "reversed"

    @property
    def bound(self) -> int:
        return REVERSED_MAX_SCORE if self.is_reversed else CORRECT_MIN_SCORE


def load_fixture(path: Path | None = None) -> dict[str, Any]:
    """Load the stored controls and their frozen context."""
    with open(path or FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_controls(path: Path | None = None) -> tuple[list[Control], list[dict[str, Any]]]:
    """Return ``(controls, context_chunks)`` from the stored fixture."""
    fixture = load_fixture(path)
    controls = [
        Control(
            id=item["id"],
            expect=item["expect"],
            label=item.get("label", ""),
            answer=item["answer"],
            recorded_scores=tuple(item.get("recorded_scores") or ()),
            recorded_in=item.get("recorded_in", ""),
        )
        for item in fixture.get("controls") or []
    ]
    return controls, list(fixture.get("context_chunks") or [])


def bound_for(expect: str) -> int:
    """The score a control of this direction may not cross."""
    return REVERSED_MAX_SCORE if expect == "reversed" else CORRECT_MIN_SCORE


def check_scores(control: Control, scores: Sequence[int | None]) -> str | None:
    """Return a failure message when a control's scores breach its bound.

    Per-run, not on the mean: a judge that rewards the reversed example even
    once is still applying the wrong rule.
    """
    usable = [score for score in scores if score is not None]
    if not usable:
        return f"{control.id}: no usable scores (every judge call failed)"
    if control.is_reversed:
        worst = max(usable)
        if worst > REVERSED_MAX_SCORE:
            return (
                f"{control.id}: reversed answer scored {worst} "
                f"(must never exceed {REVERSED_MAX_SCORE})"
            )
        return None
    worst = min(usable)
    if worst < CORRECT_MIN_SCORE:
        return (
            f"{control.id}: correct answer scored {worst} "
            f"(must never fall below {CORRECT_MIN_SCORE})"
        )
    return None


def summarise(results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-run control results and collect every breach.

    ``results`` is one dict per control with ``id``, ``expect``, ``scores`` and
    ``explanations``. The returned dict is what the CLI writes and what the task
    records: every individual score, the mean, and the pass/fail verdict.
    """
    failures: list[str] = []
    rows: list[dict[str, Any]] = []
    for result in results:
        control = Control(
            id=result["id"],
            expect=result["expect"],
            label=result.get("label", ""),
            answer="",
        )
        scores = [s for s in result.get("scores") or [] if s is not None]
        failure = check_scores(control, scores)
        if failure:
            failures.append(failure)
        rows.append({
            "id": control.id,
            "expect": control.expect,
            "scores": list(scores),
            "mean": round(statistics.mean(scores), 2) if scores else None,
            "explanations": list(result.get("explanations") or []),
            "bound": bound_for(control.expect),
            "passed": failure is None,
        })
    return {"controls": rows, "failures": failures, "passed": not failures}


def run_controls(
    *,
    runs: int = 3,
    judge_model: str = JUDGE_MODEL,
    client: Any | None = None,
    path: Path | None = None,
    question: str | None = None,
) -> dict[str, Any]:
    """Score every stored control ``runs`` times and return the summary."""
    if runs < 1:
        raise ValueError("runs must be at least 1")
    controls, chunks = load_controls(path)
    if not controls:
        raise RuntimeError("no control answers were found in the fixture")

    if client is None:
        import anthropic

        client = anthropic.Anthropic()

    question = question or (
        "How does [line~] work and what's the gotcha with its right inlet?"
    )

    results: list[dict[str, Any]] = []
    for control in controls:
        scores: list[int | None] = []
        explanations: list[str] = []
        for _ in range(runs):
            scored = score_faithfulness(
                client,
                question=question,
                answer=control.answer,
                context_chunks=chunks,
                judge_model=judge_model,
            )
            scores.append(scored.get("score"))
            explanations.append(str(scored.get("explanation") or ""))
        results.append({
            "id": control.id,
            "expect": control.expect,
            "label": control.label,
            "scores": scores,
            "explanations": explanations,
        })

    summary = summarise(results)
    summary.update({"runs": runs, "judge_model": judge_model})
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Score the recorded q_004 control answers through the faithfulness "
            "judge and fail when either direction is wrong (TASK-5.22)."
        )
    )
    parser.add_argument(
        "--runs", type=int, default=5,
        help="Times to score each control (default: 5, the task requires >= 3)",
    )
    parser.add_argument(
        "--judge", type=str, default=JUDGE_MODEL,
        help=f"Judge model (default: {JUDGE_MODEL})",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Write the per-run scores and verdict to this JSON file",
    )
    args = parser.parse_args(argv)

    summary = run_controls(runs=args.runs, judge_model=args.judge)
    for row in summary["controls"]:
        direction = (
            f"reversed, never above {REVERSED_MAX_SCORE}"
            if row["expect"] == "reversed"
            else f"correct, never below {CORRECT_MIN_SCORE}"
        )
        print(
            f"{row['id']}: scores={row['scores']} mean={row['mean']} "
            f"({direction}) — {'pass' if row['passed'] else 'FAIL'}"
        )
        for explanation in row["explanations"]:
            if explanation:
                print(f"    {explanation}")
    print(f"\nControls: {'passed' if summary['passed'] else 'FAILED'}")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
            f.write("\n")
        print(f"Written to {args.output}")

    return 0 if summary["passed"] else 1


if __name__ == "__main__":  # pragma: no cover - exercised by the CLI
    raise SystemExit(main())
