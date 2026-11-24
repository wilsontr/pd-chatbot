"""Scope-adherence evaluation over the scope dataset.

Each case names the action the gate should take (answer / answer_with_decline /
refuse_off_topic / refuse_policy). The runner classifies the question with the
production classifier — one Haiku call per case, no generation — and compares the
gate's decision with the expectation.

This is separate from the retrieval/generation golden set on purpose: retrieval
metrics must only see on-topic questions, and an off-topic case has no relevant
chunks to score.

Usage:
    python -m evaluation.scope_eval
    python -m evaluation.scope_eval --output docs/scope_eval.json --quiet
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Iterable

import scope

DEFAULT_SCOPE_DATASET = Path(__file__).parent / "scope_dataset.json"

EXPECTED_ACTIONS = {
    scope.ANSWER,
    scope.ANSWER_WITH_DECLINE,
    scope.REFUSE_OFF_TOPIC,
    scope.REFUSE_POLICY,
}


def load_scope_cases(path: Path | str | None = None) -> list[dict[str, Any]]:
    with open(path or DEFAULT_SCOPE_DATASET) as f:
        cases = json.load(f)
    if not isinstance(cases, list) or not cases:
        raise ValueError("scope dataset must be a non-empty JSON list")
    seen: set[str] = set()
    for case in cases:
        case_id = case.get("id", "")
        if not case_id or case_id in seen:
            raise ValueError(f"scope case id missing or duplicated: {case_id!r}")
        seen.add(case_id)
        if not (case.get("question") or "").strip():
            raise ValueError(f"scope case {case_id} has no question")
        if case.get("expected") not in EXPECTED_ACTIONS:
            raise ValueError(
                f"scope case {case_id} has invalid expected action {case.get('expected')!r}"
            )
    return cases


def run_scope_eval(
    cases: Iterable[dict[str, Any]] | None = None,
    classify: Callable[[str, list[dict[str, str]]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Classify each case and score whether the gate would act as expected.

    ``classify`` is injectable so the scoring can be tested without a model call.
    """
    if cases is None:
        cases = load_scope_cases()
    if classify is None:
        from rag import classify_query  # imported lazily: keeps tests model-free
        classify = classify_query

    results: list[dict[str, Any]] = []
    for case in cases:
        try:
            classification = classify(case["question"], case.get("history", []))
            predicted = scope.decide(classification.get("scope"))
        except Exception as exc:  # a failed classification counts as a miss
            classification, predicted = {}, f"error: {type(exc).__name__}"
        results.append({
            "id": case["id"],
            "category": case.get("category", ""),
            "question": case["question"],
            "expected": case["expected"],
            "predicted": predicted,
            "scope": (classification or {}).get("scope"),
            "ok": predicted == case["expected"],
        })

    passed = sum(1 for r in results if r["ok"])
    return {
        "n": len(results),
        "adherence": round(passed / len(results), 4) if results else 0.0,
        "failures": [r for r in results if not r["ok"]],
        "cases": results,
    }


def _print_table(result: dict[str, Any]) -> None:
    width = max(len(r["id"]) for r in result["cases"])
    for r in result["cases"]:
        mark = "ok  " if r["ok"] else "FAIL"
        print(f"{mark} {r['id']:<{width}}  expected={r['expected']:<20} "
              f"predicted={r['predicted']}")
    passed = sum(1 for r in result["cases"] if r["ok"])
    print(f"\nscope adherence: {passed}/{result['n']} = {result['adherence']:.0%}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score the scope gate on the scope dataset")
    parser.add_argument("--dataset", type=str, default=None)
    parser.add_argument("--output", type=str, default=None, help="Write results as JSON")
    parser.add_argument("--threshold", type=float, default=None,
                        help="Exit non-zero below this adherence (e.g. 0.9)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    result = run_scope_eval(load_scope_cases(args.dataset))
    if args.output:
        Path(args.output).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if not args.quiet:
        _print_table(result)
    if args.threshold is not None and result["adherence"] < args.threshold:
        print(f"scope adherence {result['adherence']:.2f} < {args.threshold}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
