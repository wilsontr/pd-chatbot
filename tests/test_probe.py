"""Offline tests for the one-entry agent probe (TASK-5.23).

The probe calls the model, so its API path is exercised by the A/B runs recorded
on the task; these tests pin the aggregation the before/after numbers rest on.
"""

from __future__ import annotations

import pytest

from evaluation import probe


class TestSummarise:
    def _row(self, run, *, warning=False, error=False, score=5):
        violations = []
        if warning:
            violations.append({
                "entry_id": "q_007",
                "check": "outlet-order-unsupported",
                "severity": "warning",
            })
        if error:
            violations.append({
                "entry_id": "q_007",
                "check": "object-coverage",
                "severity": "error",
            })
        return {
            "run": run,
            "claim_violations": violations,
            "scores": {"faithfulness": {"score": score}},
        }

    def test_counts_runs_with_a_warning_and_every_check(self):
        summary = probe.summarise([
            self._row(1, score=5),
            self._row(2, warning=True, score=3),
            self._row(3, warning=True, score=5),
        ])
        assert summary["runs"] == 3
        assert summary["warning_count"] == 2
        assert summary["runs_with_warning"] == 2
        assert summary["error_count"] == 0
        assert summary["faithfulness_scores"] == [5, 3, 5]
        assert summary["faithfulness_mean"] == 4.33

    def test_warnings_and_errors_are_counted_separately(self):
        summary = probe.summarise([self._row(1, warning=True, error=True)])
        assert summary["warning_count"] == 1
        assert summary["error_count"] == 1
        assert summary["runs_with_warning"] == 1

    def test_missing_scores_do_not_become_zero(self):
        row = self._row(1)
        row["scores"] = {"faithfulness": {"score": None}}
        summary = probe.summarise([row])
        assert summary["faithfulness_scores"] == []
        assert summary["faithfulness_mean"] is None

    def test_unknown_entry_id_fails_before_any_model_call(self):
        with pytest.raises(ValueError, match="no golden entry"):
            probe.probe_entry("q_does_not_exist", runs=1, judge=False)
