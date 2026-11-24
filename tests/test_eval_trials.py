"""Tests for multi-trial aggregation and the report renderer (TASK-5.21).

No API calls: the trial summaries are built by hand in the shape
``run_generation_eval`` returns, so these tests pin exactly what
``_aggregate_trials`` and ``_build_report`` do with it.
"""

from __future__ import annotations

from evaluation.evaluate import _aggregate_trials, _build_report
from evaluation.gates import generation_summary_markdown

STATS = {
    "total_entries": 30,
    "by_query_type": {"object_reference": 11, "conceptual": 12, "both": 7},
    "multi_turn_count": 2,
    "by_difficulty": {"easy": 12, "medium": 15, "hard": 3},
}
META = {"model": "m", "judge": "j", "sample": 30, "prompt": "default",
        "mode": "both", "trials": 3}


def _arm(mode: str, **overrides):
    data = {
        "n": 30,
        "mode": mode,
        "faithfulness_mean": 4.8,
        "relevance_mean": 4.9,
        "citation_correctness_mean": 4.6,
        "avg_total_cost_usd": 0.02,
        "run_total_cost_usd": 0.6 if mode == "pipeline" else 0.55,
        "p50_latency_ms": 4000.0,
        "p95_latency_ms": 9000.0,
        "avg_tool_calls": 1.5 if mode == "agent" else 0,
        "avg_retrieval_calls": 1.2 if mode == "agent" else 0,
        "claim_violation_count": 0,
    }
    data.update(overrides)
    return data


def _trial(
    pipeline_faithfulness=4.8,
    agent_faithfulness=4.8,
    n=30,
    pipeline_relevance=4.9,
    agent_relevance=4.9,
):
    return {
        "mode": "both",
        "per_model": {
            "m": _arm(
                "pipeline", faithfulness_mean=pipeline_faithfulness,
                relevance_mean=pipeline_relevance, n=n,
            ),
            "m (agent)": _arm(
                "agent", faithfulness_mean=agent_faithfulness,
                relevance_mean=agent_relevance, n=n,
            ),
        },
        "entries": [
            {"id": "q_004", "model": "m", "arm": "pipeline", "label": "m",
             "scores": {}, "claim_violations": []},
            {"id": "q_004", "model": "m", "arm": "agent", "label": "m (agent)",
             "scores": {}, "claim_violations": []},
        ],
    }


def _trials() -> list[dict]:
    return [
        _trial(4.8, 4.9),
        _trial(4.6, 4.8),
        _trial(4.7, 4.7),
    ]


class TestAggregateTrials:
    def test_means_and_standard_deviations_are_computed(self):
        merged = _aggregate_trials(_trials())
        assert merged["trials"] == 3
        pipeline = merged["per_model"]["m"]
        assert pipeline["faithfulness_mean_by_trial"] == [4.8, 4.6, 4.7]
        assert pipeline["faithfulness_mean_avg"] == 4.7
        assert pipeline["faithfulness_mean_std"] == 0.1

    def test_the_arm_label_is_preserved(self):
        # The report and the gate dispatch on "mode"; a merge that drops the
        # non-numeric fields loses the agent columns and the comparison table.
        merged = _aggregate_trials(_trials())
        assert merged["per_model"]["m"]["mode"] == "pipeline"
        assert merged["per_model"]["m (agent)"]["mode"] == "agent"

    def test_entries_are_flattened_with_their_trial_number(self):
        merged = _aggregate_trials(_trials())
        assert len(merged["entries"]) == 6
        assert {e["trial"] for e in merged["entries"]} == {1, 2, 3}
        assert merged["entries"][0]["label"] == "m"

    def test_a_skipped_entry_in_one_trial_reports_the_mean_count(self):
        merged = _aggregate_trials([_trial(n=30), _trial(n=30), _trial(n=29)])
        assert merged["per_model"]["m"]["n"] == 30  # 89/3 rounds to 30
        assert merged["per_model"]["m"]["n_by_trial"] == [30, 30, 29]


class TestMultiTrialReport:
    def _report(self, merged):
        return _build_report(
            {"generation": merged, "retrieval": {}, "scope": {"adherence": 1.0, "n": 20}},
            STATS,
            generation_meta=META,
        )

    def test_both_arms_and_the_spread_are_rendered(self):
        report = self._report(_aggregate_trials(_trials()))
        assert "3 trials per arm" in report
        assert "±" in report
        # Pipeline faithfulness 4.8/4.6/4.7 -> 4.70 ± 0.10, agent 4.9/4.8/4.7 -> 4.80 ± 0.10
        assert "| 4.70 ± 0.10 |" in report
        assert "| 4.80 ± 0.10 |" in report

    def test_the_pipeline_vs_agent_table_survives_the_merge(self):
        report = self._report(_aggregate_trials(_trials()))
        assert "### Pipeline vs agent" in report
        assert "| m (pipeline) |" in report
        assert "| m (agent) |" in report
        assert "Tool calls" in report
        # Both arms keep a tool-call figure: 0 for the pipeline, 1.5 for the agent.
        assert "| 1.50 |" in report

    def test_p50_and_p95_are_reported_for_both_arms(self):
        report = self._report(_aggregate_trials(_trials()))
        assert "p95 latency" in report
        assert "4.0s" in report
        assert "9.0s" in report
        assert report.count("| 4.0s | 9.0s |") == 4  # two rows in each of two tables

    def test_the_run_cost_is_the_per_trial_mean_times_trials(self):
        report = self._report(_aggregate_trials(_trials()))
        # (0.60 pipeline + 0.55 agent) per trial x 3 trials = 3.45
        assert "$3.4500" in report
        assert "across 3 trials" in report

    def test_a_single_trial_report_has_no_spread(self):
        report = _build_report(
            {"generation": {"per_model": {"m": _arm("pipeline")}},
             "retrieval": {}, "scope": {}},
            STATS,
            generation_meta={**META, "trials": 1},
        )
        assert "±" not in report
        assert "trials per arm" not in report

    def test_the_reading_says_matches_when_differences_are_within_noise(self):
        # Deltas +0.2, -0.1, +0.1: the mean difference is smaller than its own
        # spread, so no verdict beyond "matches" is supportable.
        trials = [
            _trial(pipeline_faithfulness=4.7, agent_faithfulness=agent)
            for agent in (4.9, 4.6, 4.8)
        ]
        report = self._report(_aggregate_trials(trials))
        assert "Reading:" in report
        assert "matches the pipeline" in report

    def test_the_reading_says_beats_when_a_difference_persists(self):
        trials = [
            _trial(pipeline_relevance=4.8, agent_relevance=5.0)
            for _ in range(3)
        ]
        report = self._report(_aggregate_trials(trials))
        assert "beats the pipeline on relevance" in report
        assert "relevance +0.20 ± 0.00" in report

    def test_the_reading_says_trails_when_the_agent_is_consistently_worse(self):
        trials = [
            _trial(pipeline_faithfulness=5.0, agent_faithfulness=4.4)
            for _ in range(3)
        ]
        report = self._report(_aggregate_trials(trials))
        assert "trails the pipeline on faithfulness" in report


class TestMultiTrialJobSummary:
    def test_summary_states_trials_and_the_total_cost(self):
        summary = generation_summary_markdown(
            {"generation": _aggregate_trials(_trials())},
            sample_size=30,
        )
        assert "**Trials:** 3" in summary
        assert "$3.4500" in summary
        assert "across 3 trials" in summary

    def test_summary_keeps_the_pipeline_gate_verdict_on_a_merged_summary(self):
        summary = generation_summary_markdown(
            {"generation": _aggregate_trials(_trials())},
            sample_size=30,
        )
        assert "### Gate: passed" in summary


class TestClaimSectionWiring:
    def test_the_scored_answer_count_is_per_entry_not_per_trial(self):
        from evaluation.evaluate import _claim_check_section

        entries = [
            {"id": "q_001", "label": "m", "claim_violations": []},
            {"id": "q_001", "label": "m", "claim_violations": []},
            {"id": "q_002", "label": "m", "claim_violations": []},
        ]
        lines = _claim_check_section({"entries": entries})
        assert any("2 scored answers" in line for line in lines)

    def test_errors_and_warnings_are_counted_separately(self):
        from evaluation.evaluate import _claim_check_section

        entries = [{"id": "q_004", "label": "m", "claim_violations": [
            {"entry_id": "q_004", "check": "line-list-order", "detail": "d",
             "evidence": "e", "severity": "error"},
            {"entry_id": "q_004", "check": "outlet-order-unsupported", "detail": "d",
             "evidence": "w", "severity": "warning"},
        ]}]
        text = "\n".join(_claim_check_section({"entries": entries}))
        assert "1 error(s), 1 warning(s)" in text
