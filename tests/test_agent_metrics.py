"""Unit tests for agent-level eval metrics and CI thresholds (TASK-5.7).

No API calls or network needed: exercises loop error/recovery tracking,
generation and patch eval aggregation, multi-trial recovery pooling,
calibrated agent thresholds, CLI gating, and report rendering.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
import types
from types import SimpleNamespace
from typing import Any

import pytest

from agent.chat import answer_question
from agent.llm import ModelDelta, ModelTurn, ToolCallRequest
from agent.loop import AgentLoop
from agent.tools import ToolOutcome, ToolRegistry, ToolSpec
from evaluation.gates import (
    AGENT_THRESHOLDS,
    MIN_RECOVERY_ERROR_TASKS,
    check_agent_thresholds,
    generation_summary_markdown,
    recovery_stats,
)
from evaluation.patch_eval import summarize as summarize_patches
from evaluation.patch_eval import to_markdown as patches_to_markdown

CHUNK = {
    "id": "p1",
    "heading_path": "osc~",
    "text": "osc~ is a cosine oscillator.",
    "url": "https://example.test/osc~",
    "source": "iem_reference",
    "content_type": "object_reference",
    "object_name": "osc~",
}


class ScriptedClient:
    def __init__(self, turns: list[dict[str, Any]]):
        self._turns = list(turns)

    async def stream_turn(self, request):
        script = self._turns.pop(0)
        text = script.get("text", "")
        if text:
            yield ModelDelta(text)
        content: list[dict[str, Any]] = []
        if text:
            content.append({"type": "text", "text": text})
        calls = []
        for raw in script.get("tool_calls", []):
            content.append({
                "type": "tool_use",
                "id": raw["id"],
                "name": raw["name"],
                "input": raw.get("input", {}),
            })
            calls.append(ToolCallRequest(raw["id"], raw["name"], raw.get("input", {})))
        yield ModelTurn(
            content=content,
            text=text,
            tool_calls=tuple(calls),
            stop_reason="tool_use" if calls else "end_turn",
            usage={"input": 10, "output": 5, "total": 15},
        )


class SequenceTool:
    def __init__(self, name: str, outcomes: list[ToolOutcome]):
        self.spec = ToolSpec(name=name, description=name, input_schema={"type": "object"})
        self._outcomes = list(outcomes)

    def run(self, arguments):
        return self._outcomes.pop(0)


class TestLoopErrorRecovery:
    def test_clean_run_records_zero_errors(self):
        client = ScriptedClient([
            {"tool_calls": [{"id": "c1", "name": "search_docs", "input": {"query": "osc~"}}]},
            {"text": "osc~ is a cosine oscillator."},
        ])
        tool = SequenceTool("search_docs", [
            ToolOutcome(content="doc", summary="1 section", chunks=(CHUNK,)),
        ])
        loop = AgentLoop(client, ToolRegistry([tool]))
        asyncio.run(self._drain(loop))
        assert loop.state.tool_errors == 0
        assert loop.state.recovered_errors == 0
        assert loop.state.unresolved_errors == 0

    def test_empty_search_then_successful_retry_counts_as_recovered(self):
        client = ScriptedClient([
            {"tool_calls": [{"id": "c1", "name": "search_docs", "input": {"query": "nope"}}]},
            {"tool_calls": [{"id": "c2", "name": "search_docs", "input": {"query": "osc~"}}]},
            {"text": "osc~ is a cosine oscillator."},
        ])
        tool = SequenceTool("search_docs", [
            ToolOutcome(content="none", summary="no match", ok=True, chunks=()),
            ToolOutcome(content="doc", summary="1 section", ok=True, chunks=(CHUNK,)),
        ])
        ans = asyncio.run(answer_question(
            "What is osc~?", client=client, tools=ToolRegistry([tool]), execution=False,
        ))
        assert ans.tool_errors == 1
        assert ans.recovered_errors == 1
        assert ans.unresolved_errors == 0

    def test_non_clean_patch_run_then_clean_run_counts_as_recovered(self):
        client = ScriptedClient([
            {"tool_calls": [{"id": "c1", "name": "run_patch", "input": {"name": "bell"}}]},
            {"tool_calls": [{"id": "c2", "name": "run_patch", "input": {"name": "bell"}}]},
            {"text": "Fixed the patch."},
        ])
        tool = SequenceTool("run_patch", [
            ToolOutcome(
                content="err", summary="ran bell: 1 error(s)", ok=True,
                data={"name": "bell", "ran_cleanly": False, "errors": ["couldn't create"]},
            ),
            ToolOutcome(
                content="ok", summary="ran bell: clean", ok=True,
                data={"name": "bell", "ran_cleanly": True, "errors": []},
            ),
        ])
        loop = AgentLoop(client, ToolRegistry([tool]))
        asyncio.run(self._drain(loop))
        assert loop.state.tool_errors == 1
        assert loop.state.recovered_errors == 1
        assert loop.state.unresolved_errors == 0

    def test_unrecovered_tool_failure_stays_unresolved(self):
        client = ScriptedClient([
            {"tool_calls": [{"id": "c1", "name": "write_patch", "input": {"name": "bell"}}]},
            {"text": "Could not build patch."},
        ])
        tool = SequenceTool("write_patch", [
            ToolOutcome(content="bad schema", summary="invalid", ok=False),
        ])
        loop = AgentLoop(client, ToolRegistry([tool]))
        asyncio.run(self._drain(loop))
        assert loop.state.tool_errors == 1
        assert loop.state.recovered_errors == 0
        assert loop.state.unresolved_errors == 1

    @staticmethod
    async def _drain(loop: AgentLoop):
        async for _ in loop.stream("question"):
            pass


class TestGenerationTaskMetrics:
    def test_computes_task_success_and_recovery_for_both_arms(self, monkeypatch, tmp_path):
        import evaluation.generation as gen
        from evaluation.dataset import GoldenDataset

        path = tmp_path / "ds.json"
        path.write_text(json.dumps([
            {"id": "t_1", "question": "q1"},
            {"id": "t_2", "question": "q2"},
            {"id": "t_3", "question": "q3"},
            {"id": "t_4", "question": "q4"},
        ]))
        dataset = GoldenDataset(path)

        monkeypatch.setattr(
            gen, "retrieve", lambda q, h=None: ([CHUNK], {"query_type": "both"}),
        )
        monkeypatch.setattr(
            gen, "_generate",
            lambda *a, **k: ("pipeline answer", {"input_tokens": 10, "output_tokens": 5, "cache_read_tokens": 0}),
        )

        # Agent outcomes:
        # t_1: clean pass (no error)
        # t_2: hit 1 tool error, recovered and passed
        # t_3: hit 1 tool error, judge scored faithfulness=2 -> failed & unrecovered
        # t_4: hit 1 tool error, retrieved 0 chunks -> skipped & unrecovered
        agent_runs = iter([
            SimpleNamespace(answer="a1", chunks=[CHUNK], usage={"input": 10, "output": 5, "cache_read": 0},
                            tool_calls=1, retrieval_calls=1, turns=2, tool_errors=0, recovered_errors=0, unresolved_errors=0),
            SimpleNamespace(answer="a2", chunks=[CHUNK], usage={"input": 10, "output": 5, "cache_read": 0},
                            tool_calls=2, retrieval_calls=1, turns=3, tool_errors=1, recovered_errors=1, unresolved_errors=0),
            SimpleNamespace(answer="a3", chunks=[CHUNK], usage={"input": 10, "output": 5, "cache_read": 0},
                            tool_calls=2, retrieval_calls=1, turns=3, tool_errors=1, recovered_errors=0, unresolved_errors=1),
            SimpleNamespace(answer="a4", chunks=[], usage={"input": 10, "output": 5, "cache_read": 0},
                            tool_calls=1, retrieval_calls=0, turns=2, tool_errors=1, recovered_errors=0, unresolved_errors=1),
        ])
        monkeypatch.setattr(gen, "_agent_answer", lambda *a, **k: next(agent_runs))

        def fake_judge(*args, **kwargs):
            score = 2 if kwargs["answer"] == "a3" else 5
            return {
                "faithfulness": {"score": score, "explanation": ""},
                "relevance": {"score": 5, "explanation": ""},
                "citation_correctness": {"score": 5, "explanation": ""},
            }

        monkeypatch.setattr(gen, "evaluate_generation", fake_judge)
        summary = gen.run_generation_eval(dataset=dataset, mode="both")

        pipe = summary["per_model"]["claude-haiku-5-5"]
        agent = summary["per_model"]["claude-haiku-5-5 (agent)"]

        assert pipe["task_success_count"] == 4
        assert pipe["task_attempts"] == 4
        assert pipe["task_success_rate"] == 1.0

        # 2 of 4 attempted tasks succeeded (t_1 and t_2); t_3 failed judge, t_4 skipped
        assert agent["task_success_count"] == 2
        assert agent["task_attempts"] == 4
        assert agent["task_success_rate"] == 0.5
        # 3 tasks had errors (t_2, t_3, t_4); 1 recovered (t_2)
        assert agent["tasks_with_errors"] == 3
        assert agent["tasks_recovered"] == 1
        assert agent["recovery_rate"] == round(1 / 3, 4)
        assert agent["avg_tool_calls"] == round(5 / 3, 2)

        agent_entries = [e for e in summary["entries"] if e["arm"] == "agent"]
        assert [e["task_success"] for e in agent_entries] == [True, True, False]
        assert [e["recovered"] for e in agent_entries] == [False, True, False]


class TestPatchEvalRecovery:
    def test_summarize_and_markdown_include_recovery_rate(self):
        base = {
            "id": "p_01", "trial": 1, "wrote_patch": True, "clean": True,
            "latency_s": 1.0, "cost_usd": 0.01, "tool_calls": 3, "runs": 2,
        }
        rows = [
            {**base, "arm": "with", "passed": True, "had_error": True, "recovered": True},
            {**base, "arm": "with", "passed": True, "had_error": True, "recovered": True},
            {**base, "arm": "with", "passed": False, "had_error": True, "recovered": False},
            {**base, "arm": "without", "passed": True, "had_error": False, "recovered": False},
        ]
        summary = summarize_patches(rows)
        assert summary["with"]["tasks_with_errors"] == 3
        assert summary["with"]["tasks_recovered"] == 2
        assert summary["with"]["recovery_rate"] == round(2 / 3, 4)
        assert summary["without"]["recovery_rate"] is None

        md = patches_to_markdown(summary, rows)
        assert "| Recovery rate |" in md
        assert "67% (2/3)" in md
        assert "n/a (0 errors)" in md


def _agent_row(**overrides):
    data = {
        "n": 10,
        "mode": "agent",
        "faithfulness_mean": 4.7,
        "relevance_mean": 4.9,
        "citation_correctness_mean": 4.5,
        "task_success_rate": 0.85,
        "avg_tool_calls": 1.5,
        "avg_total_cost_usd": 0.012,
        "run_total_cost_usd": 0.12,
        "p50_latency_ms": 5500.0,
        "p95_latency_ms": 8000.0,
        "tasks_with_errors": 0,
        "tasks_recovered": 0,
    }
    data.update(overrides)
    return data


class TestAgentThresholds:
    def test_baseline_agent_passes(self):
        gen = {"per_model": {"m (agent)": _agent_row()}}
        assert check_agent_thresholds(gen) == []

    def test_missing_agent_arm_fails(self):
        gen = {"per_model": {"m": _agent_row(mode="pipeline")}}
        failures = check_agent_thresholds(gen)
        assert len(failures) == 1
        assert "no agent-arm scores" in failures[0]

    def test_each_agent_threshold_catches_regression(self):
        assert any("task_success_rate" in f for f in check_agent_thresholds(
            {"per_model": {"m (agent)": _agent_row(task_success_rate=0.5)}}
        ))
        assert any("avg_tool_calls" in f for f in check_agent_thresholds(
            {"per_model": {"m (agent)": _agent_row(avg_tool_calls=5.0)}}
        ))
        assert any("avg_cost_usd" in f for f in check_agent_thresholds(
            {"per_model": {"m (agent)": _agent_row(avg_total_cost_usd=0.05)}}
        ))
        assert any("p95_latency" in f for f in check_agent_thresholds(
            {"per_model": {"m (agent)": _agent_row(p95_latency_ms=25000.0)}}
        ))

    def test_recovery_rate_requires_min_error_tasks(self):
        # Single error (0/1) is below MIN_RECOVERY_ERROR_TASKS -> does not fail gate.
        below_min = _agent_row(tasks_with_errors=MIN_RECOVERY_ERROR_TASKS - 1, tasks_recovered=0)
        assert check_agent_thresholds({"per_model": {"m (agent)": below_min}}) == []

        # At MIN_RECOVERY_ERROR_TASKS, 0 recovered fails; >= 50% passes.
        failing = _agent_row(tasks_with_errors=MIN_RECOVERY_ERROR_TASKS, tasks_recovered=0)
        failures = check_agent_thresholds({"per_model": {"m (agent)": failing}})
        assert len(failures) == 1 and "recovery_rate" in failures[0]

        passing = _agent_row(tasks_with_errors=2, tasks_recovered=1)
        assert check_agent_thresholds({"per_model": {"m (agent)": passing}}) == []

    def test_multi_trial_recovery_pools_counts_across_trials(self):
        merged_arm = _agent_row(
            tasks_with_errors_by_trial=[0, 2, 1],
            tasks_recovered_by_trial=[0, 2, 0],
            task_success_rate_avg=0.85,
            avg_tool_calls_avg=1.6,
            avg_total_cost_usd_avg=0.012,
            p95_latency_ms_avg=8000.0,
        )
        merged_arm.pop("tasks_with_errors")
        merged_arm.pop("tasks_recovered")
        rate, recovered, errors = recovery_stats(merged_arm)
        assert (recovered, errors) == (2, 3)
        assert rate == pytest.approx(2 / 3)
        assert check_agent_thresholds({"per_model": {"m (agent)": merged_arm}}) == []

        # 1/3 pooled recovery is below 0.50 -> fails gate.
        merged_arm["tasks_recovered_by_trial"] = [0, 1, 0]
        failures = check_agent_thresholds({"per_model": {"m (agent)": merged_arm}})
        assert len(failures) == 1 and "1/3" in failures[0]

    def test_summary_markdown_gates_agent_metrics_when_enabled(self):
        gen = {"per_model": {
            "m": _agent_row(mode="pipeline", avg_tool_calls=0),
            "m (agent)": _agent_row(avg_tool_calls=4.2),
        }}
        md = generation_summary_markdown(
            {"generation": gen}, sample_size=10, include_agent=True
        )
        assert "### Gate: FAILED" in md
        assert "avg_tool_calls 4.20" in md
        assert "Agent gate:" in md
        assert "Task success" in md
        assert "Recovery" in md


_FAKE_RAG_NAMES = (
    "retrieve", "vector_search", "bm25_search", "child_chunks", "parent_lookup",
    "collection", "HistoryItem", "Chunk", "_build_chat_messages", "SYSTEM_PROMPT",
)


@pytest.fixture
def evaluate_module(monkeypatch):
    fake_rag = types.ModuleType("rag")
    for name in _FAKE_RAG_NAMES:
        setattr(fake_rag, name, None)
    monkeypatch.setitem(sys.modules, "rag", fake_rag)
    for name in ("evaluation.evaluate", "evaluation.generation", "evaluation.retrieval"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    module = importlib.import_module("evaluation.evaluate")
    module.run_scope_eval = lambda *a, **k: {
        "n": 20, "adherence": 1.0, "failures": [], "cases": [],
    }
    yield module
    for name in ("evaluation.evaluate", "evaluation.generation", "evaluation.retrieval"):
        sys.modules.pop(name, None)


class TestCliAndReportWiring:
    def test_cli_gate_agent_fails_on_agent_metric_breach(
        self, evaluate_module, monkeypatch, tmp_path
    ):
        both = {
            "mode": "both",
            "per_model": {
                "m": _agent_row(mode="pipeline", avg_tool_calls=0),
                "m (agent)": _agent_row(task_success_rate=0.50),
            },
            "entries": [],
        }
        monkeypatch.setattr(evaluate_module, "run_generation_eval", lambda **_: both)
        report_path = tmp_path / "EVAL.md"
        code = evaluate_module.main([
            "--generation", "--mode", "both", "--sample", "10",
            "--gate", "--gate-agent", "--report", str(report_path), "--quiet",
        ])
        assert code == 1
        report = report_path.read_text()
        assert "Task success" in report
        assert "Recovery" in report
        assert "50%" in report
        assert "n/a (0 errors)" in report
        assert "Agent gate (CI `--gate-agent`, docs mode, no execution)" in report
        assert f"{AGENT_THRESHOLDS['task_success_rate']:.0%}" in report

    def test_cli_gate_agent_passes_when_metrics_meet_thresholds(
        self, evaluate_module, monkeypatch
    ):
        both = {
            "mode": "both",
            "per_model": {
                "m": _agent_row(mode="pipeline", avg_tool_calls=0),
                "m (agent)": _agent_row(tasks_with_errors=2, tasks_recovered=2),
            },
            "entries": [],
        }
        monkeypatch.setattr(evaluate_module, "run_generation_eval", lambda **_: both)
        code = evaluate_module.main([
            "--generation", "--mode", "both", "--sample", "10",
            "--gate", "--gate-agent", "--quiet",
        ])
        assert code == 0
