"""Rubric-contract and control-fixture tests for the faithfulness judge (TASK-5.22).

The judge's inlet-order belief was wrong in both directions: it rewarded a
reversed ``[line~]`` list example and penalised a correct left-to-right one. The
rubric now states Pd's documented rule, and ``evaluation/judge_controls.py``
pins both directions against recorded answers. These tests check the contract
offline — no API calls, like the rest of the suite.
"""

from __future__ import annotations

import pytest

from evaluation import judge_controls
from evaluation.judge import FAITHFULNESS_PROMPT
from model_config import JUDGE_MODEL


class TestRubricContract:
    @staticmethod
    def _prompt() -> str:
        # Whitespace-normalised: the prompt is hard-wrapped, and the contract is
        # about the words, not where the line breaks fall.
        return " ".join(FAITHFULNESS_PROMPT.lower().split())

    def test_rubric_states_the_left_to_right_inlet_rule(self):
        prompt = self._prompt()
        assert "inlets from left to right" in prompt
        assert "first value goes to the leftmost inlet" in prompt

    def test_rubric_keeps_the_right_to_left_rule_about_outlets(self):
        prompt = self._prompt()
        assert "right-to-left rule is about outlets, not inlets" in prompt
        assert "says nothing about which inlet a list element reaches" in prompt

    def test_rubric_tells_the_judge_to_penalise_a_reversed_example(self):
        prompt = self._prompt()
        assert "is reversed and must be penalised" in prompt
        # ... and not to penalise a correct left-to-right description.
        assert "do not penalise a correct left-to-right description" in prompt

    def test_rubric_does_not_treat_separate_messages_or_outlet_mapping_as_lists(self):
        # The first control run exposed the judge over-applying the rule to a
        # patch wired with separate messages and to `[unpack]`'s outlet mapping.
        prompt = self._prompt()
        assert "separate messages wired to separate inlets are not a list example" in prompt
        assert "must not be read as a reversed list" in prompt
        assert "does not require any particular outlet to fire first" in prompt
        assert "in their position order is not an inlet-order error" in prompt

    @pytest.mark.parametrize(
        "literal",
        ["1000 0.5", "500 1000", "`1000 0.5`", "`500 1000`", "time, then target"],
    )
    def test_rubric_does_not_name_a_golden_entry_answer(self, literal):
        assert literal not in FAITHFULNESS_PROMPT

    def test_rubric_still_penalises_unsupported_object_behaviour(self):
        # The clarification must not become a blanket excuse for beyond-docs
        # object specifics; TASK-5.23 still measures those.
        prompt = self._prompt()
        assert "does not relax the rest of the rubric" in prompt
        assert "still an unflagged claim beyond the context" in prompt


class TestControlFixture:
    def test_fixture_carries_both_directions_with_their_context(self):
        controls, chunks = judge_controls.load_controls()
        by_expect = {control.expect: control for control in controls}
        assert set(by_expect) == {"reversed", "correct"}
        assert chunks, "controls must be judged against the stored context"
        assert all(chunk.get("text") for chunk in chunks)

    def test_fixture_records_the_reversed_claim(self):
        controls, _ = judge_controls.load_controls()
        reversed_control = next(c for c in controls if c.expect == "reversed")
        assert "`1000 0.5`" in reversed_control.answer
        assert "time, then target" in reversed_control.answer
        # The recording it came from is named so the fixture is auditable.
        assert "notes_520" in reversed_control.recorded_in

    def test_fixture_records_the_correct_claim(self):
        controls, _ = judge_controls.load_controls()
        correct_control = next(c for c in controls if c.expect == "correct")
        assert "`500 1000`" in correct_control.answer
        assert "first value goes to the leftmost inlet" in correct_control.answer

    def test_fixture_is_judged_by_the_pinned_judge_model(self):
        fixture = judge_controls.load_fixture()
        assert fixture["judge_model"] == JUDGE_MODEL


class TestBounds:
    def test_reversed_answer_above_three_fails(self):
        control = judge_controls.Control("q_004_reversed", "reversed", "", "")
        assert judge_controls.check_scores(control, [5, 5, 5])
        assert judge_controls.check_scores(control, [2, 4, 3])
        assert judge_controls.check_scores(control, [2, 3, 3]) is None

    def test_correct_answer_below_four_fails(self):
        control = judge_controls.Control("q_004_correct", "correct", "", "")
        assert judge_controls.check_scores(control, [2, 2, 2])
        assert judge_controls.check_scores(control, [5, 4, 3])
        assert judge_controls.check_scores(control, [5, 4, 4]) is None

    def test_missing_scores_fail_rather_than_pass_silently(self):
        control = judge_controls.Control("q_004_reversed", "reversed", "", "")
        assert judge_controls.check_scores(control, [None, None])

    def test_summary_records_every_score_and_the_breach(self):
        summary = judge_controls.summarise([
            {"id": "q_004_reversed", "expect": "reversed", "scores": [4, 5, 3],
             "explanations": ["a", "b", "c"]},
            {"id": "q_004_correct", "expect": "correct", "scores": [5, 4, 5],
             "explanations": []},
        ])
        assert not summary["passed"]
        assert summary["controls"][0]["scores"] == [4, 5, 3]
        assert summary["controls"][0]["mean"] == 4.0
        assert not summary["controls"][0]["passed"]
        assert summary["controls"][1]["passed"]
        assert any("q_004_reversed" in failure for failure in summary["failures"])


class TestRunControls:
    def test_every_control_is_scored_the_requested_number_of_times(self, monkeypatch):
        calls = []

        def fake_score(client, *, question, answer, context_chunks, judge_model, **kwargs):
            calls.append(answer)
            return {"score": 2 if "time, then target" in answer else 5,
                    "explanation": "ok"}

        monkeypatch.setattr(judge_controls, "score_faithfulness", fake_score)
        summary = judge_controls.run_controls(runs=3, client=object())

        assert len(calls) == 6
        assert summary["passed"]
        assert [row["scores"] for row in summary["controls"]] == [[2, 2, 2], [5, 5, 5]]

    def test_a_wrong_judge_direction_is_reported(self, monkeypatch):
        # The pre-fix behaviour: reversed scored 5, correct scored 2.
        def fake_score(client, *, question, answer, context_chunks, judge_model, **kwargs):
            return {"score": 5 if "time, then target" in answer else 2,
                    "explanation": "wrong"}

        monkeypatch.setattr(judge_controls, "score_faithfulness", fake_score)
        summary = judge_controls.run_controls(runs=1, client=object())

        assert not summary["passed"]
        assert len(summary["failures"]) == 2
