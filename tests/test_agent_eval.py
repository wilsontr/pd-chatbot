"""Tests for the eval harness's pipeline-vs-agent support (TASK-5.1).

No API calls: the generation and agent arms are monkeypatched, so these tests
cover the arm labelling, the report section and the gate's arm filtering.
"""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from evaluation.gates import check_generation_thresholds, generation_summary_markdown


def _model(**overrides):
    data = {
        "n": 5,
        "faithfulness_mean": 4.8,
        "relevance_mean": 4.9,
        "citation_correctness_mean": 4.5,
        "avg_total_cost_usd": 0.02,
        "p50_latency_ms": 4200.0,
        "run_total_cost_usd": 0.1,
        "mode": "pipeline",
    }
    data.update(overrides)
    return data


class TestGateArms:
    def test_agent_arm_is_not_gated_by_default(self):
        generation = {"per_model": {"m (agent)": _model(mode="agent", faithfulness_mean=3.0)}}
        # Only an agent arm exists, so the gate has nothing it is calibrated for.
        assert check_generation_thresholds(generation)
        assert "pipeline arm" in check_generation_thresholds(generation)[0]

    def test_agent_arm_is_skipped_when_a_pipeline_arm_passes(self):
        generation = {"per_model": {
            "m": _model(),
            "m (agent)": _model(mode="agent", faithfulness_mean=2.0),
        }}
        assert check_generation_thresholds(generation) == []

    def test_agent_arm_can_be_gated_explicitly(self):
        generation = {"per_model": {
            "m": _model(),
            "m (agent)": _model(mode="agent", faithfulness_mean=2.0),
        }}
        failures = check_generation_thresholds(generation, include_agent=True)
        assert len(failures) == 1
        assert "m (agent)" in failures[0]

    def test_summary_lists_agent_rows_without_claiming_the_gate(self):
        summary = generation_summary_markdown(
            {"generation": {"per_model": {
                "m": _model(),
                "m (agent)": _model(mode="agent", avg_tool_calls=1.8),
            }}},
            sample_size=5,
        )
        assert "m (agent)" in summary
        assert "### Gate: passed" in summary
        # The pass detail must name the gated pipeline arm only once.
        assert summary.count("- m: faithfulness") == 1


class TestReport:
    def _results(self):
        return {
            "retrieval": {
                "ablation": True,
                "hybrid_rrf": {"recall": {"recall@5": 0.8}, "mrr": 0.736, "n": 30},
                "vector_only": {"recall": {"recall@5": 0.72}, "mrr": 0.6, "n": 30},
                "bm25_only": {"recall": {"recall@5": 0.36}, "mrr": 0.26, "n": 30},
                "agent_tool": {"recall": {"recall@5": 0.84}, "mrr": 0.75, "n": 30},
            },
            "generation": {
                "mode": "both",
                "per_model": {
                    "m": _model(),
                    "m (agent)": _model(
                        mode="agent", faithfulness_mean=4.9, avg_tool_calls=1.8,
                        avg_retrieval_calls=1.2,
                    ),
                },
            },
            "scope": {"adherence": 1.0, "n": 20, "failures": []},
        }

    def test_report_compares_the_two_arms(self):
        from evaluation.evaluate import _build_report

        report = _build_report(
            self._results(),
            {"total_entries": 30, "by_query_type": {}, "multi_turn_count": 0,
             "by_difficulty": {}},
            generation_meta={"model": "m", "judge": "j", "sample": 10,
                             "prompt": "default", "mode": "both"},
        )
        assert "### Pipeline vs agent" in report
        assert "| m (pipeline) |" in report
        assert "| m (agent) |" in report
        assert "Tool calls" in report
        assert "agent \u2212 pipeline" in report
        assert report.count("Both arms ran on every entry") == 1

    def test_retrieval_row_and_comparison_for_agent_tool_calls(self):
        from evaluation.evaluate import _build_report

        report = _build_report(
            self._results(),
            {"total_entries": 30, "by_query_type": {}, "multi_turn_count": 0,
             "by_difficulty": {}},
        )
        assert "Agent tool calls" in report
        assert "84.00%" in report
        assert "one model turn" in report

    def test_pipeline_only_report_has_no_comparison_section(self):
        from evaluation.evaluate import _build_report

        results = self._results()
        results["generation"] = {"mode": "pipeline", "per_model": {"m": _model()}}
        results["retrieval"].pop("agent_tool")
        report = _build_report(
            results,
            {"total_entries": 30, "by_query_type": {}, "multi_turn_count": 0,
             "by_difficulty": {}},
        )
        assert "### Pipeline vs agent" not in report
        assert "Agent tool calls" not in report


class TestGenerationArms:
    @pytest.fixture
    def gen(self):
        import evaluation.generation as gen_module

        return gen_module

    def test_invalid_mode_raises(self, gen, tmp_path):
        from evaluation.dataset import GoldenDataset

        path = tmp_path / "ds.json"
        path.write_text(json.dumps([{"id": "t_1", "question": "q1"}]))
        with pytest.raises(ValueError):
            gen.run_generation_eval(dataset=GoldenDataset(path), mode="nonsense")

    def test_agent_mode_labels_the_arm_and_records_tool_calls(
        self, gen, monkeypatch, tmp_path
    ):
        from evaluation.dataset import GoldenDataset

        path = tmp_path / "ds.json"
        path.write_text(json.dumps([{"id": "t_1", "question": "q1"}]))
        dataset = GoldenDataset(path)

        monkeypatch.setattr(
            gen, "_agent_answer",
            lambda model, entry, system_prompt=None: SimpleNamespace(
                answer="an answer",
                chunks=[{"id": "p1", "heading_path": "h", "text": "t"}],
                usage={"input": 100, "output": 40, "cache_read": 0},
                tool_calls=2,
                retrieval_calls=1,
                turns=3,
            ),
        )
        monkeypatch.setattr(
            gen, "evaluate_generation",
            lambda *a, **k: {d: {"score": 4, "explanation": ""}
                             for d in ("faithfulness", "relevance", "citation_correctness")},
        )
        summary = gen.run_generation_eval(dataset=dataset, mode="agent")
        label = "claude-haiku-5-5 (agent)"
        model = summary["per_model"][label]
        assert model["mode"] == "agent"
        assert model["n"] == 1
        assert model["avg_tool_calls"] == 2
        assert model["avg_retrieval_calls"] == 1
        assert model["avg_input_tokens"] == 100
        assert summary["entries"][0]["arm"] == "agent"

    def test_both_mode_runs_two_arms_on_the_same_entry(
        self, gen, monkeypatch, tmp_path
    ):
        from evaluation.dataset import GoldenDataset

        path = tmp_path / "ds.json"
        path.write_text(json.dumps([{"id": "t_1", "question": "q1"}]))
        dataset = GoldenDataset(path)

        monkeypatch.setattr(
            gen, "retrieve",
            lambda q, h=None: ([{"id": "p1", "heading_path": "h", "text": "t"}],
                               {"query_type": "both"}),
        )
        monkeypatch.setattr(
            gen, "_generate",
            lambda *a, **k: ("pipeline answer",
                             {"input_tokens": 10, "output_tokens": 5,
                              "cache_read_tokens": 0}),
        )
        monkeypatch.setattr(
            gen, "_agent_answer",
            lambda model, entry, system_prompt=None: SimpleNamespace(
                answer="agent answer",
                chunks=[{"id": "p1", "heading_path": "h", "text": "t"}],
                usage={"input": 10, "output": 5, "cache_read": 0},
                tool_calls=1,
                retrieval_calls=1,
                turns=2,
            ),
        )
        monkeypatch.setattr(
            gen, "evaluate_generation",
            lambda *a, **k: {d: {"score": 4, "explanation": ""}
                             for d in ("faithfulness", "relevance", "citation_correctness")},
        )
        summary = gen.run_generation_eval(dataset=dataset, mode="both")
        assert set(summary["per_model"]) == {"claude-haiku-5-5", "claude-haiku-5-5 (agent)"}
        assert summary["mode"] == "both"
        assert len(summary["entries"]) == 2

    def test_both_arms_get_the_same_patch_count_canonicalisation(
        self, gen, monkeypatch, tmp_path
    ):
        # The repair is answer canonicalisation, not an agent capability: the
        # pipeline baseline must be judged on the same canonical diagram.
        from evaluation.dataset import GoldenDataset

        path = tmp_path / "ds.json"
        path.write_text(json.dumps([{"id": "t_1", "question": "q1"}]))
        dataset = GoldenDataset(path)
        chunk = {
            "id": "p1",
            "object_name": "snapshot~",
            "heading_path": "snapshot~",
            "text": (
                "Object: snapshot~\n\nInlets:\n"
                "1st | bang - convert a signal to a float.\n\n"
                "Outlets:\n1st | float - the converted signal at every bang.\n"
            ),
        }
        wrong_patch = (
            "```pd-patch\n"
            '{"objects": [{"id": "0", "type": "obj", "text": "snapshot~", '
            '"inlets": 2, "outlets": 1}], "connections": []}\n'
            "```"
        )
        monkeypatch.setattr(
            gen, "retrieve", lambda q, h=None: ([chunk], {"query_type": "both"})
        )
        monkeypatch.setattr(
            gen, "_generate",
            lambda *a, **k: (wrong_patch,
                             {"input_tokens": 1, "output_tokens": 1,
                              "cache_read_tokens": 0}),
        )
        monkeypatch.setattr(
            gen, "_agent_answer",
            lambda model, entry, system_prompt=None: SimpleNamespace(
                answer=wrong_patch,
                chunks=[chunk],
                usage={"input": 1, "output": 1, "cache_read": 0},
                tool_calls=1, retrieval_calls=1, turns=2,
            ),
        )
        judged: list[str] = []

        def fake_judge(*args, **kwargs):
            judged.append(kwargs["answer"])
            return {d: {"score": 4, "explanation": ""}
                    for d in ("faithfulness", "relevance", "citation_correctness")}

        monkeypatch.setattr(gen, "evaluate_generation", fake_judge)
        gen.run_generation_eval(dataset=dataset, mode="both")

        assert len(judged) == 2
        for answer in judged:
            raw = answer.split("```pd-patch")[1].split("```")[0].strip()
            assert json.loads(raw)["objects"][0]["inlets"] == 1

    def test_agent_entry_without_chunks_is_counted_as_skipped(
        self, gen, monkeypatch, tmp_path
    ):
        from evaluation.dataset import GoldenDataset

        path = tmp_path / "ds.json"
        path.write_text(json.dumps([{"id": "t_1", "question": "q1"}]))
        monkeypatch.setattr(
            gen, "_agent_answer",
            lambda model, entry, system_prompt=None: SimpleNamespace(
                answer="docs don't cover it",
                chunks=[],
                usage={"input": 1, "output": 1, "cache_read": 0},
                tool_calls=1, retrieval_calls=0, turns=2,
            ),
        )
        summary = gen.run_generation_eval(
            dataset=GoldenDataset(path), mode="agent"
        )
        model = summary["per_model"]["claude-haiku-5-5 (agent)"]
        assert model["n"] == 0
        assert model["skipped_no_context"] == 1


class TestRetrievalAgentArm:
    def test_agent_strategy_is_opt_in(self, monkeypatch):
        import evaluation.retrieval as retrieval

        calls = {"agent": 0}

        def fake_agent(entry):
            calls["agent"] += 1
            return retrieval.RetrievalResult(entry.id, [], set())

        monkeypatch.setattr(retrieval, "_run_agent_tool", fake_agent)
        monkeypatch.setattr(
            retrieval, "_run_hybrid",
            lambda entry: retrieval.RetrievalResult(entry.id, [], set()),
        )
        monkeypatch.setattr(
            retrieval, "_only_entries_with_relevant",
            lambda dataset: [SimpleNamespace(id="t_1", question="q1", history=[],
                                             query_type="both", relevant_chunk_ids=set())],
        )
        dataset = SimpleNamespace(stats={})
        assert "agent_tool" not in retrieval.run_retrieval_eval(dataset, ablation=False)
        assert calls["agent"] == 0

        result = retrieval.run_retrieval_eval(dataset, ablation=False, agent=True)
        assert calls["agent"] == 1
        assert "agent_tool" in result
