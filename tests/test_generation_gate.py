"""Tests for the sampled generation eval gate (TASK-3).

Two layers:
- evaluation.gates is pure (no rag/chroma/API clients), so the threshold and
  summary logic is tested directly.
- evaluation.evaluate wiring is tested with the generation run monkeypatched, so
  no API calls and no key are needed; the real rag module is replaced by a stub
  before evaluate is imported (same trick as tests/test_feedback.py).
"""

import importlib
import json
import sys
import types

import pytest

from evaluation.gates import (
    CI_JUDGE_MODEL,
    CI_MODEL,
    CI_SAMPLE_SIZE,
    GENERATION_THRESHOLDS,
    check_generation_thresholds,
    generation_summary_markdown,
)


def _summary(faithfulness=4.8, relevance=5.0, citation=4.6, n=10, **extra):
    model = {
        "n": n,
        "faithfulness_mean": faithfulness,
        "relevance_mean": relevance,
        "citation_correctness_mean": citation,
        "avg_cost_usd": 0.0219,
        "avg_judge_cost_usd": 0.0091,
        "avg_total_cost_usd": 0.031,
        "run_total_cost_usd": 0.31,
    }
    model.update(extra)
    return {"per_model": {CI_MODEL: model}, "entries": []}


class TestThresholds:
    def test_documented_thresholds_gate_faithfulness_and_relevance_only(self):
        assert set(GENERATION_THRESHOLDS) == {"faithfulness", "relevance"}

    def test_passing_summary_has_no_failures(self):
        assert check_generation_thresholds(_summary()) == []

    def test_faithfulness_regression_fails(self):
        failures = check_generation_thresholds(_summary(faithfulness=3.2))
        assert len(failures) == 1
        assert "faithfulness" in failures[0] and "3.20" in failures[0]

    def test_relevance_regression_fails(self):
        failures = check_generation_thresholds(_summary(relevance=4.0))
        assert len(failures) == 1
        assert "relevance" in failures[0]

    def test_both_dimensions_can_fail_together(self):
        failures = check_generation_thresholds(
            _summary(faithfulness=2.0, relevance=3.0)
        )
        assert len(failures) == 2

    def test_citation_is_reported_but_not_gated(self):
        # Observed min 2 with mean 4.6 on the baseline sample: too noisy to gate.
        assert check_generation_thresholds(_summary(citation=1.0)) == []

    def test_boundary_is_inclusive_pass(self):
        assert check_generation_thresholds(
            _summary(faithfulness=4.0, relevance=4.5)
        ) == []

    def test_missing_generation_results_fail(self):
        assert check_generation_thresholds({}) == [
            "no generation scores were produced (is the sample empty?)"
        ]

    def test_missing_dimension_score_fails(self):
        summary = _summary()
        del summary["per_model"][CI_MODEL]["faithfulness_mean"]
        failures = check_generation_thresholds(summary)
        assert any("no usable score" in f for f in failures)

    def test_custom_thresholds_are_honoured(self):
        assert check_generation_thresholds(
            _summary(faithfulness=4.2), {"faithfulness": 4.5}
        ) != []

    def test_multi_trial_summary_shape_is_readable(self):
        # --trials suffixes merged fields with _avg (see _aggregate_trials).
        multi_trial = {"per_model": {CI_MODEL: {
            "n": 10,
            "faithfulness_mean_avg": 4.7,
            "relevance_mean_avg": 4.9,
            "citation_correctness_mean_avg": 4.5,
            "avg_total_cost_usd_avg": 0.03,
        }}}
        assert check_generation_thresholds(multi_trial) == []
        md = generation_summary_markdown(
            {"generation": multi_trial}, sample_size=10
        )
        assert "| 4.70 | 4.90 |" in md
        assert "$0.0300" in md


class TestSummaryMarkdown:
    def test_states_sample_models_cost_and_verdict(self):
        md = generation_summary_markdown(
            {"generation": _summary()},
            sample_size=CI_SAMPLE_SIZE,
            model=CI_MODEL,
            judge_model=CI_JUDGE_MODEL,
        )
        assert f"`{CI_MODEL}`" in md
        assert f"`{CI_JUDGE_MODEL}`" in md
        assert f"**Sample:** {CI_SAMPLE_SIZE}" in md
        assert "$0.0310" in md  # cost/query column
        assert "$0.3100" in md  # measured run cost
        assert "Gate: passed" in md
        assert "faithfulness >= 4" in md

    def test_failing_summary_lists_each_failure(self):
        md = generation_summary_markdown(
            {"generation": _summary(faithfulness=3.0, relevance=4.0)},
            sample_size=10,
        )
        assert "Gate: FAILED" in md
        assert md.count("threshold") >= 2

    def test_empty_results_do_not_crash(self):
        md = generation_summary_markdown({}, sample_size=10)
        assert "Gate: FAILED" in md
        assert "no generation scores" in md


_FAKE_RAG_NAMES = (
    "retrieve", "vector_search", "bm25_search", "child_chunks", "parent_lookup",
    "collection", "HistoryItem", "Chunk", "_build_chat_messages", "SYSTEM_PROMPT",
)


@pytest.fixture
def evaluate_module(monkeypatch):
    """Import evaluation.evaluate with a stub rag, so no index or keys are needed."""
    fake_rag = types.ModuleType("rag")
    for name in _FAKE_RAG_NAMES:
        setattr(fake_rag, name, None)
    monkeypatch.setitem(sys.modules, "rag", fake_rag)
    for name in ("evaluation.evaluate", "evaluation.generation", "evaluation.retrieval"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    module = importlib.import_module("evaluation.evaluate")
    yield module
    for name in ("evaluation.evaluate", "evaluation.generation", "evaluation.retrieval"):
        sys.modules.pop(name, None)


class TestCliWiring:
    def test_gate_passes_and_returns_zero(self, evaluate_module, monkeypatch, tmp_path):
        monkeypatch.setattr(
            evaluate_module, "run_generation_eval", lambda **_: _summary()
        )
        summary_path = tmp_path / "summary.md"
        code = evaluate_module.main([
            "--generation", "--sample", "10", "--gate", "--quiet",
            "--summary", str(summary_path),
        ])
        assert code == 0
        text = summary_path.read_text()
        assert "Gate: passed" in text
        assert "**Sample:** 10" in text

    def test_gate_failure_returns_non_zero(self, evaluate_module, monkeypatch):
        monkeypatch.setattr(
            evaluate_module, "run_generation_eval",
            lambda **_: _summary(faithfulness=2.5),
        )
        code = evaluate_module.main([
            "--generation", "--sample", "10", "--gate", "--quiet",
        ])
        assert code == 1

    def test_gate_requires_a_generation_run(self, evaluate_module, monkeypatch):
        monkeypatch.setattr(evaluate_module, "run_retrieval_eval", lambda *a, **k: {})
        code = evaluate_module.main(["--retrieval", "--gate", "--quiet", "--no-ablation"])
        assert code == 2

    def test_report_publishes_generation_alongside_retrieval(
        self, evaluate_module, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            evaluate_module, "run_generation_eval", lambda **_: _summary()
        )
        monkeypatch.setattr(
            evaluate_module, "run_retrieval_eval", lambda *a, **k: {
                "ablation": False,
                "hybrid_rrf": {"recall": {"recall@5": 0.7667}, "mrr": 0.7556,
                               "ndcg": {"ndcg@5": 0.7195}},
            },
        )
        report_path = tmp_path / "EVAL.md"
        code = evaluate_module.main([
            "--all", "--sample", "10", "--report", str(report_path), "--quiet",
        ])
        assert code == 0
        report = report_path.read_text()
        assert "## Retrieval" in report
        assert "## Generation (LLM-as-Judge)" in report
        assert "76.67%" in report            # retrieval table still rendered
        assert "`claude-sonnet-4-6`" in report  # sampled-eval caption
        assert "$0.0310" in report           # cost/query column
        assert "faithfulness >= 4" in report  # generation gate documented

    def test_summary_appends_so_github_step_summary_accumulates(
        self, evaluate_module, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            evaluate_module, "run_generation_eval", lambda **_: _summary()
        )
        summary_path = tmp_path / "summary.md"
        summary_path.write_text("earlier step output\n")
        for _ in range(2):
            evaluate_module.main([
                "--generation", "--sample", "10", "--quiet",
                "--summary", str(summary_path),
            ])
        text = summary_path.read_text()
        assert text.startswith("earlier step output\n")
        assert text.count("## Generation eval (sampled)") == 2
