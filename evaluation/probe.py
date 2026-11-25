"""Probe one golden entry through the agent loop, repeatedly (TASK-5.23).

The 30-entry x 3-trial eval answers "did the whole suite move?"; a prompt fix
needs the narrower question "does this entry still make the claim, and what did
the model retrieve before it did?". This module runs one entry N times, records
every tool call with its arguments and returned headings, the answer, the
deterministic claim checks and the judge scores, then aggregates the per-run
counts.

It is deliberately separate from ``evaluation.generation`` (which owns the
reportable eval) so a probe never looks like a published measurement: same loop,
same tools, same checks, but one entry and no report.

Usage::

    python -m evaluation.probe q_007 --runs 3
    python -m evaluation.probe q_007 --runs 3 --system-prompt-file .tmp/variant.txt
    python -m evaluation.probe q_004 --runs 5 --no-judge

The A/B in TASK-5.23 compares variants by the ``outlet-order-unsupported``
warning rate in the aggregate; the full answers and tool calls are kept so the
result can be audited rather than trusted.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from model_config import ANSWER_MODEL, JUDGE_MODEL

from . import claims
from .dataset import GoldenDataset, GoldenEntry
from .judge import evaluate_generation


@dataclass
class ToolStep:
    """One tool call and, when it ran, what it returned."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    ok: bool | None = None
    summary: str = ""
    headings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "arguments": self.arguments,
            "ok": self.ok,
            "summary": self.summary,
            "headings": self.headings,
        }


async def _run_agent(
    entry: GoldenEntry,
    *,
    system_prompt: str | None = None,
    model: str = ANSWER_MODEL,
) -> tuple[str, list[dict[str, Any]], list[ToolStep], int]:
    """Run one entry through the same loop the app and the eval use."""
    from agent.chat import default_config, default_registry
    from agent.events import ToolCall, ToolResult
    from agent.llm import AnthropicModelClient
    from agent.loop import AgentLoop
    from agent.patch_counts import repair_patch
    from agent.patches import append_patches
    from agent.workspace import PatchWorkspace

    overrides = {"system_prompt": system_prompt} if system_prompt else {}
    config = default_config(execution=False, model=model, **overrides)
    workspace = PatchWorkspace()
    loop = AgentLoop(
        AnthropicModelClient(), default_registry(workspace, execution=False), config
    )

    steps: list[ToolStep] = []
    by_id: dict[str, ToolStep] = {}
    async for event in loop.stream(entry.question, history=entry.history):
        if isinstance(event, ToolCall):
            step = ToolStep(name=event.name, arguments=dict(event.arguments))
            steps.append(step)
            by_id[event.id] = step
        elif isinstance(event, ToolResult):
            step = by_id.get(event.id)
            if step is not None:
                step.ok = event.ok
                step.summary = event.summary
                step.headings = list(
                    (event.data or {}).get("headings") or []
                )
    state = loop.state
    # Same canonicalisation as agent.chat.answer_question (the path the eval and
    # the app use), so a probe measures the shipped answer rather than the raw
    # model text. The loop itself stays free of docs/patch policy.
    for item in workspace.entries.values():
        item.patch = repair_patch(item.patch, list(state.chunks))
    answer = append_patches(state.answer, workspace.patches())
    return answer, list(state.chunks), steps, state.turns


def probe_entry(
    entry_id: str,
    *,
    runs: int = 3,
    system_prompt: str | None = None,
    judge_model: str = JUDGE_MODEL,
    judge: bool = True,
    dataset: GoldenDataset | None = None,
) -> dict[str, Any]:
    """Run one golden entry ``runs`` times and return the per-run record."""
    if runs < 1:
        raise ValueError("runs must be at least 1")
    dataset = dataset or GoldenDataset()
    entry = next((e for e in dataset.entries if e.id == entry_id), None)
    if entry is None:
        raise ValueError(f"no golden entry with id {entry_id!r}")

    client = None
    if judge:
        import anthropic

        client = anthropic.Anthropic()

    rows: list[dict[str, Any]] = []
    for run in range(1, runs + 1):
        answer, chunks, steps, turns = asyncio.run(
            _run_agent(entry, system_prompt=system_prompt)
        )
        violations = [
            v.as_dict() for v in claims.check_answer(entry.id, answer, chunks)
        ]
        row: dict[str, Any] = {
            "run": run,
            "answer": answer,
            "tool_calls": [step.as_dict() for step in steps],
            "turns": turns,
            "chunks_retrieved": len(chunks),
            "headings": [
                (chunk.get("heading_path") or "")[:160] for chunk in chunks
            ],
            "claim_violations": violations,
        }
        if judge:
            scores = evaluate_generation(
                client,
                question=entry.question,
                answer=answer,
                context_chunks=chunks,
                judge_model=judge_model,
            )
            row["scores"] = {
                dimension: {
                    "score": result.get("score"),
                    "explanation": result.get("explanation"),
                }
                for dimension, result in scores.items()
            }
        rows.append(row)

    return {
        "entry_id": entry_id,
        "question": entry.question,
        "runs": runs,
        "judged": judge,
        "judge_model": judge_model if judge else None,
        "records": rows,
        "summary": summarise(rows),
    }


def summarise(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Per-run claim counts and mean faithfulness for one probe."""
    rows = list(rows)
    checks = [
        violation.get("check")
        for row in rows
        for violation in row.get("claim_violations") or []
    ]
    warnings = [
        violation.get("check")
        for row in rows
        for violation in row.get("claim_violations") or []
        if violation.get("severity") == claims.WARNING
    ]
    errors = [
        violation.get("check")
        for row in rows
        for violation in row.get("claim_violations") or []
        if violation.get("severity") != claims.WARNING
    ]
    scores = [
        row["scores"]["faithfulness"]["score"]
        for row in rows
        if (row.get("scores") or {}).get("faithfulness", {}).get("score") is not None
    ]
    return {
        "runs": len(rows),
        "claim_checks": checks,
        "warning_count": len(warnings),
        "error_count": len(errors),
        "runs_with_warning": sum(
            1
            for row in rows
            if any(
                violation.get("severity") == claims.WARNING
                for violation in row.get("claim_violations") or []
            )
        ),
        "faithfulness_scores": scores,
        "faithfulness_mean": (
            round(sum(scores) / len(scores), 2) if scores else None
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run one golden entry through the agent loop N times and record its "
            "tool calls, claim checks and judge scores (TASK-5.23)."
        )
    )
    parser.add_argument("entry_id", help="Golden entry id, e.g. q_007")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument(
        "--system-prompt-file", type=str, default=None,
        help="Score this prompt instead of the shipped AGENT_SYSTEM_PROMPT",
    )
    parser.add_argument("--judge", type=str, default=JUDGE_MODEL)
    parser.add_argument("--no-judge", action="store_true")
    parser.add_argument("--output", type=str, default=None)
    args = parser.parse_args(argv)

    prompt = None
    if args.system_prompt_file:
        prompt = Path(args.system_prompt_file).read_text(encoding="utf-8")

    result = probe_entry(
        args.entry_id,
        runs=args.runs,
        system_prompt=prompt,
        judge_model=args.judge,
        judge=not args.no_judge,
    )
    summary = result["summary"]
    print(
        f"{args.entry_id}: {summary['runs_with_warning']}/{summary['runs']} runs "
        f"flagged warnings {sorted(set(summary['claim_checks']))}; "
        f"faithfulness={summary['faithfulness_scores']}"
    )
    for row in result["records"]:
        print(
            f"  run {row['run']}: tool calls="
            f"{[(step['name'], step['arguments'].get('query')) for step in row['tool_calls']]} "
            f"warnings={[v['check'] for v in row['claim_violations']]}"
        )
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
            f.write("\n")
        print(f"Written to {args.output}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by the CLI
    raise SystemExit(main())
