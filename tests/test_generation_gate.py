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
from types import SimpleNamespace

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
    def test_documented_thresholds(self):
        assert set(GENERATION_THRESHOLDS) == {
            "faithfulness", "relevance", "citation_correctness",
        }
        assert GENERATION_THRESHOLDS["citation_correctness"] == 4.0

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

    def test_citation_regression_fails(self):
        # Gated since the citation-hardened prompt made the mean stable at ~4.58;
        # the threshold leaves room for judge noise but not for invented URLs.
        failures = check_generation_thresholds(_summary(citation=3.5))
        assert len(failures) == 1
        assert "citation_correctness" in failures[0]

    def test_citation_at_threshold_passes(self):
        assert check_generation_thresholds(_summary(citation=4.0)) == []

    def test_boundary_is_inclusive_pass(self):
        assert check_generation_thresholds(
            _summary(faithfulness=4.0, relevance=4.5, citation=4.0)
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
        assert "citation_correctness >= 4" in md

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
    # Scope scoring calls the real classifier; tests that do not exercise it get
    # a passing result so the gate's generation logic stays isolated.
    module.run_scope_eval = lambda *a, **k: {
        "n": 20, "adherence": 1.0, "failures": [], "cases": [],
    }
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
        assert f"`{CI_MODEL}`" in report  # sampled-eval caption
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


class TestPromptVariantsAndLatency:
    def test_system_prompt_file_reaches_the_eval(
        self, evaluate_module, monkeypatch, tmp_path
    ):
        captured = {}

        def fake_run(**kwargs):
            captured.update(kwargs)
            return _summary()

        monkeypatch.setattr(evaluate_module, "run_generation_eval", fake_run)
        prompt = tmp_path / "variant.txt"
        prompt.write_text("You are a citation-strict assistant.")
        code = evaluate_module.main([
            "--generation", "--sample", "3", "--quiet",
            "--system-prompt-file", str(prompt),
        ])
        assert code == 0
        assert captured["system_prompt"] == "You are a citation-strict assistant."

    def test_default_run_passes_no_prompt_override(self, evaluate_module, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            evaluate_module, "run_generation_eval",
            lambda **kwargs: (captured.update(kwargs), _summary())[1],
        )
        evaluate_module.main(["--generation", "--sample", "3", "--quiet"])
        assert captured["system_prompt"] is None

    def test_generate_threads_the_override_into_the_message_builder(
        self, evaluate_module, monkeypatch
    ):
        gen = sys.modules["evaluation.generation"]
        seen = {}

        def fake_build(question, chunks, history, system_prompt=None):
            seen["system_prompt"] = system_prompt
            return [{"type": "text", "text": "sys"}], [{"role": "user", "content": "q"}]

        monkeypatch.setattr(gen, "_build_chat_messages", fake_build)

        class FakeMessages:
            def create(self, **kwargs):
                return SimpleNamespace(
                    content=[SimpleNamespace(type="text", text="the answer")],
                    usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                )

        client = SimpleNamespace(messages=FakeMessages())
        answer, usage = gen._generate(
            client, "claude-haiku-5-5", "q", [], [], system_prompt="CUSTOM PROMPT"
        )
        assert answer == "the answer"
        assert seen["system_prompt"] == "CUSTOM PROMPT"
        assert usage["input_tokens"] == 10

    def test_run_generation_eval_records_latency_per_model(
        self, evaluate_module, monkeypatch, tmp_path
    ):
        gen = sys.modules["evaluation.generation"]
        dataset_file = tmp_path / "ds.json"
        dataset_file.write_text(json.dumps([
            {"id": "t_1", "question": "q1"},
            {"id": "t_2", "question": "q2"},
        ]))
        from evaluation.dataset import GoldenDataset

        monkeypatch.setattr(
            gen, "retrieve",
            lambda q, h=None: ([{"heading_path": "h", "text": "t"}], {"query_type": "both"}),
        )
        monkeypatch.setattr(
            gen, "_generate",
            lambda *a, **k: ("answer", {"input_tokens": 10, "output_tokens": 5,
                                        "cache_read_tokens": 0}),
        )
        monkeypatch.setattr(
            gen, "evaluate_generation",
            lambda *a, **k: {d: {"score": 4, "explanation": ""}
                             for d in ("faithfulness", "relevance", "citation_correctness")},
        )
        summary = gen.run_generation_eval(dataset=GoldenDataset(dataset_file), sample=2)
        model = summary["per_model"][CI_MODEL]
        assert model["n"] == 2
        assert "avg_latency_ms" in model and "p50_latency_ms" in model
        assert "p95_latency_ms" in model
        assert all("latency_ms" in e for e in summary["entries"])


class TestLatencyAggregation:
    def test_percentiles_and_means(self, evaluate_module):
        lat = sys.modules.get("evaluation.latency") or importlib.import_module("evaluation.latency")
        summary = lat.aggregate_latencies([
            {"ttft_ms": 100.0, "total_ms": 1000.0, "chars": 10},
            {"ttft_ms": 200.0, "total_ms": 2000.0, "chars": 20},
            {"ttft_ms": 300.0, "total_ms": 3000.0, "chars": 30},
            {"ttft_ms": 400.0, "total_ms": 4000.0, "chars": 40},
        ])
        assert summary["n"] == 4
        assert summary["ttft_mean_ms"] == 250.0
        assert summary["ttft_p50_ms"] == 250.0
        assert summary["ttft_p95_ms"] == 400.0
        assert summary["total_p95_ms"] == 4000.0
        assert summary["avg_chars"] == 25

    def test_empty_measurements(self, evaluate_module):
        lat = importlib.import_module("evaluation.latency")
        assert lat.aggregate_latencies([]) == {"n": 0}

    def test_missing_ttft_is_tolerated(self, evaluate_module):
        lat = importlib.import_module("evaluation.latency")
        summary = lat.aggregate_latencies([
            {"ttft_ms": None, "total_ms": 1000.0, "chars": 5},
            {"ttft_ms": 100.0, "total_ms": 2000.0, "chars": 15},
        ])
        assert summary["ttft_mean_ms"] == 100.0
        assert summary["total_mean_ms"] == 1500.0
