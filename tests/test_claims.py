"""Tests for the deterministic claim checks and their eval wiring (TASK-5.20).

No API calls: every check is offline, and the eval wiring is exercised with the
generation arm and the judge monkeypatched, the same way ``test_agent_eval.py``
does it.
"""

from __future__ import annotations

import json

import pytest

from agent.prompt import AGENT_SYSTEM_PROMPT, SYSTEM_PROMPT
from evaluation import claims
from evaluation.gates import check_claim_violations, generation_summary_markdown


def _chunk(id, *, object_name=None, heading="", text="", url=None):
    return {
        "id": id,
        "object_name": object_name,
        "heading_path": heading,
        "text": text,
        "url": url or f"https://pd.iem.sh/objects/{id}/",
    }


LINE_CHUNKS = [
    _chunk("line~", object_name="line~", heading="line~",
           text="A list of two floats distributes the value over the inlets, as "
                "usual in Pd. The right inlet does not remember old values.",
           url="https://pd.iem.sh/objects/line~/"),
]
PACK_CHUNKS = [
    _chunk("pack", object_name="pack", heading="pack",
           text="The pack object outputs a concatenated list from a series of inputs.",
           url="https://pd.iem.sh/objects/pack/"),
    _chunk("unpack", object_name="unpack", heading="unpack",
           text="unpack a list of numbers and distribute them to separate outlets.",
           url="https://pd.iem.sh/objects/unpack/"),
]
ORDER_CHUNK = _chunk(
    "msp_parent_31",
    heading="Chapter 2: Theory of operation > 2.4. Messages > 2.4.4. Hot and cold "
            "inlets and right to left outlet order",
    text="By convention, all objects in Pd, when sending messages out more than "
         "one outlet, do so from right to left.",
    url="https://msp.ucsd.edu/Pd_documentation/x2.htm",
)
SUBPATCH_CHUNKS = [
    _chunk("msp_parent_52", heading="Chapter 2: Theory of operation > 2.8. Subpatches",
           text="Subpatches are a way of encapsulating parts of a patch.",
           url="https://msp.ucsd.edu/Pd_documentation/"),
]


class TestInletOrder:
    @pytest.mark.parametrize("text", [
        "A list distributes values right to left across the inlets.",
        "Pd fills the inlets from the right, so the last value hits the left inlet.",
        "Values reach the rightmost inlet first when you send a list.",
    ])
    def test_flags_right_to_left_inlet_claims(self, text):
        assert claims.check_inlet_order(text)

    @pytest.mark.parametrize("text", [
        "All objects send their outlets right to left.",
        "The trigger object fires its outlets right to left.",
        "A list distributes values left to right: the first value sets the left inlet.",
        "Send the time to the right inlet first, then the target to the left inlet.",
        # Recorded false positive from the first seeded run: naming the right-hand
        # inlet is not an ordering claim.
        "The time comes from the right inlet or the first argument.",
        # Recorded false positive from the second seeded run: a sentence about
        # MAX's ordering is not a claim about Pd's.
        "The MSP manual notes that in Max, outputs to several inlets go out right "
        "to left by inlet position.",
        # Recorded false positive: answers cite the section title verbatim.
        "(Source: MSP Manual, 2.4.4 Hot and cold inlets and right to left outlet order.)",
    ])
    def test_allows_the_outlet_rule_and_correct_advice(self, text):
        assert claims.check_inlet_order(text) is None


class TestLineListOrder:
    def test_flags_the_recorded_time_before_target_phrasing(self):
        # The phrasing recorded on TASK-5.20 from the TASK-5.1 run.
        assert claims.check_line_list_order(
            "You can send the time and the target together, for example a list `100 500`."
        )

    def test_flags_the_recorded_parenthetical_phrasing(self):
        # The same error, re-recorded from current code before the prompt fix.
        assert claims.check_line_list_order(
            "Send a list such as `1000 0.5` (time, then target, with the list "
            "distributed across the inlets as described above)."
        )

    def test_flags_a_self_contradicting_numeric_example(self):
        # "ramp to 0.5 over 1000 ms" cannot be the list `1000 0.5`: Pd fills the
        # inlets left to right, so that list ramps to 1000 over 0.5 ms.
        assert claims.check_line_list_order(
            "Example of a correct ramp to 0.5 over 1000 ms, using a message box `1000 0.5`."
        )

    def test_allows_the_correct_element_order(self):
        assert claims.check_line_list_order(
            "Use the list `0.5 1000` to ramp to 0.5 over 1000 ms."
        ) is None
        assert claims.check_line_list_order(
            "A list of two floats is distributed across the inlets left to right."
        ) is None

    def test_allows_separate_messages_advice(self):
        assert claims.check_line_list_order(
            "Send 1000 to the right inlet first, then 0.5 to the left inlet."
        ) is None

    def test_allows_the_separate_messages_phrasing_recording(self):
        # Recorded false positive from the first seeded run: this is the correct
        # advice and mentions list after target after time.
        assert claims.check_line_list_order(
            "In practice, send the time first and then the target as two separate "
            "messages, not as a single list."
        ) is None


class TestCitationCheck:
    def test_flags_a_url_no_excerpt_contains(self):
        answer = "Sources:\n- https://pd.iem.sh/objects/line~/"
        assert claims.check_citations(answer, []) == ["https://pd.iem.sh/objects/line~/"]

    def test_allows_urls_from_the_excerpts(self):
        answer = "Sources:\n- https://pd.iem.sh/objects/line~/"
        assert claims.check_citations(answer, LINE_CHUNKS) == []

    def test_tolerates_a_trailing_slash_difference(self):
        answer = "See https://pd.iem.sh/objects/line~."
        assert claims.check_citations(answer, LINE_CHUNKS) == []


class TestCoverage:
    def test_flags_an_object_the_excerpts_do_not_cover(self):
        answer = "The outlets of [unpack] fire right to left."
        assert claims.check_coverage(answer, SUBPATCH_CHUNKS, ["unpack"]) == ["unpack"]

    def test_allows_an_object_the_excerpts_cover(self):
        answer = "The outlets of [unpack] fire right to left."
        assert claims.check_coverage(answer, PACK_CHUNKS, ["unpack"]) == []

    def test_ignores_objects_the_answer_does_not_discuss(self):
        assert claims.check_coverage("Use [pack].", PACK_CHUNKS, ["pack", "unpack"]) == []


class TestCheckAnswer:
    def test_pinned_line_entry_flags_the_recorded_failure(self):
        violations = claims.check_answer(
            "q_004",
            "Send the time and the target together, for example a list `100 500`.",
            LINE_CHUNKS,
        )
        assert [v.check for v in violations] == ["line-list-order"]

    def test_pinned_line_entry_accepts_the_reference_behaviour(self):
        violations = claims.check_answer(
            "q_004",
            "Send a ramp time of 1000 ms to the right inlet first, then the target "
            "0.5 to the left inlet. The right inlet does not remember old values. "
            "Sources:\n- https://pd.iem.sh/objects/line~/",
            LINE_CHUNKS,
        )
        assert violations == []

    def test_pinned_pack_entry_flags_a_claim_its_excerpts_do_not_cover(self):
        violations = claims.check_answer(
            "q_007",
            "[pack] joins values and [unpack] splits them; its outlets fire right to left.",
            SUBPATCH_CHUNKS,
        )
        checks = [v.check for v in violations]
        assert checks[0] == "object-coverage"
        assert violations[0].severity == claims.ERROR
        assert "unpack" in violations[0].evidence

    def test_pinned_pack_entry_accepts_a_covered_answer(self):
        violations = claims.check_answer(
            "q_007",
            "[pack] joins values and [unpack] splits them; its outlets fire right to left. "
            "Sources:\n- https://pd.iem.sh/objects/pack/\n"
            "- https://pd.iem.sh/objects/unpack/",
            PACK_CHUNKS + [ORDER_CHUNK],
        )
        assert violations == []

    def test_an_unsupported_ordering_claim_is_a_warning_not_an_error(self):
        # True Pd, but the retrieved IEM references never state it: reported, not gated.
        violations = claims.check_answer(
            "q_007",
            "[pack] joins values and [unpack] splits them; its outlets fire right to left.",
            PACK_CHUNKS,
        )
        assert [v.check for v in violations] == ["outlet-order-unsupported"]
        assert violations[0].severity == claims.WARNING

    def test_violations_are_json_serialisable(self):
        violations = claims.check_answer("q_004", "a list distributes right to left", [])
        assert json.loads(json.dumps([v.as_dict() for v in violations]))[0]["check"]


class TestPromptContract:
    """The prompt rule the fix depends on, pinned against a careless rewrite."""

    def test_both_arms_share_the_inlet_order_rule(self):
        rule = "fills an object's inlets left to right"
        assert rule in SYSTEM_PROMPT
        assert rule in AGENT_SYSTEM_PROMPT

    def test_prompt_separates_the_outlet_rule_from_inlet_order(self):
        assert "right-to-left rule in Pd is about outlets, not inlets" in SYSTEM_PROMPT

    def test_agent_prompt_is_the_shipped_prompt_plus_tool_rules(self):
        assert AGENT_SYSTEM_PROMPT.startswith(SYSTEM_PROMPT)


class TestGate:
    def _generation(self, *, violations):
        return {
            "per_model": {
                "m": {"n": 2, "faithfulness_mean": 4.8, "claim_violation_count": 0},
                "m (agent)": {"n": 2, "mode": "agent", "claim_violation_count": len(violations)},
            },
            "entries": [
                {"entry_id": "q_001", "label": "m", "claim_violations": []},
                {"entry_id": "q_004", "label": "m (agent)", "claim_violations": violations},
            ],
        }

    def test_no_violations_passes(self):
        assert check_claim_violations(self._generation(violations=[])) == []

    def test_a_violation_on_the_agent_arm_still_fails(self):
        failures = check_claim_violations(self._generation(violations=[{
            "entry_id": "q_004",
            "check": "line-list-order",
            "detail": "reversed list example",
            "evidence": "time, then target",
        }]))
        assert len(failures) == 1
        assert "m (agent)" in failures[0]
        assert "q_004" in failures[0]

    def test_the_same_violation_repeated_across_trials_is_reported_once(self):
        violation = {"entry_id": "q_004", "check": "inlet-order", "detail": "d"}
        generation = {"entries": [
            {"entry_id": "q_004", "label": "m", "claim_violations": [violation]},
            {"entry_id": "q_004", "label": "m", "claim_violations": [violation]},
        ], "per_model": {}}
        assert len(check_claim_violations(generation)) == 1

    def test_summary_failure_names_the_claim_check(self):
        summary = generation_summary_markdown(
            {"generation": self._generation(violations=[{
                "entry_id": "q_004", "check": "line-list-order",
                "detail": "reversed list example", "evidence": "time, then target",
            }])},
            sample_size=2,
        )
        assert "### Gate: FAILED" in summary
        assert "claim check" in summary
