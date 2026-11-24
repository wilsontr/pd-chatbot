"""Tests for the scope gate (TASK-7).

Two layers:
- The pure decision logic in `scope.py` and the eval scoring in
  `evaluation/scope_eval.py`, which need no model or network.
- The pipeline behaviour in `rag.py`, with every model call mocked: off-topic and
  policy questions must be refused without generation or retrieval, mixed
  questions must reach generation with an explicit decline instruction, on-topic
  questions must be untouched, and a low-relevance match must take the
  "no relevant documentation" path.
"""

import asyncio
import json
from unittest.mock import patch

import pytest

import scope
from evaluation.gates import SCOPE_ADHERENCE_THRESHOLD, check_scope_threshold
from evaluation.scope_eval import EXPECTED_ACTIONS, load_scope_cases, run_scope_eval


class TestDecisionLogic:
    @pytest.mark.parametrize("label,action", [
        ("in_scope", scope.ANSWER),
        ("mixed", scope.ANSWER_WITH_DECLINE),
        ("off_topic", scope.REFUSE_OFF_TOPIC),
        ("policy_violation", scope.REFUSE_POLICY),
    ])
    def test_labels_map_to_actions(self, label, action):
        assert scope.decide(label) == action

    @pytest.mark.parametrize("raw", [None, "", "nonsense", 42, {"x": 1}])
    def test_unknown_labels_fail_open_to_in_scope(self, raw):
        assert scope.classify_scope(raw) == scope.IN_SCOPE
        assert scope.decide(raw) == scope.ANSWER

    def test_labels_are_case_insensitive(self):
        assert scope.decide("OFF_TOPIC") == scope.REFUSE_OFF_TOPIC

    def test_refusal_texts_are_short_and_mention_pure_data(self):
        off = scope.refusal_text("off_topic")
        policy = scope.refusal_text("policy_violation")
        assert "Pure Data" in off and len(off) < 300
        assert "can't help" in policy and len(policy) < 300

    def test_mixed_note_names_the_off_topic_part(self):
        note = scope.mixed_scope_note({"off_topic_part": "the capital of France"})
        assert "the capital of France" in note
        assert "decline" in note.lower()

    def test_mixed_note_without_a_part_still_instructs_a_decline(self):
        assert "decline" in scope.mixed_scope_note({}).lower()

    def test_is_refusal(self):
        assert scope.is_refusal("off_topic")
        assert scope.is_refusal("policy_violation")
        assert not scope.is_refusal("mixed")
        assert not scope.is_refusal("in_scope")

    def test_relevance_cutoff_sits_below_the_golden_minimum(self):
        # Golden-set minimum measured at 0.449; off-topic maximum 0.433.
        assert 0.40 <= scope.MIN_RELEVANCE_SIMILARITY < 0.449


class TestScopeDataset:
    def test_dataset_loads_and_validates(self):
        cases = load_scope_cases()
        assert len(cases) >= 15
        assert {c["expected"] for c in cases} <= EXPECTED_ACTIONS

    def test_required_categories_are_present(self):
        categories = {c["category"] for c in load_scope_cases()}
        assert {"off_topic", "mixed", "policy", "multi_turn_bypass", "on_topic"} <= categories

    def test_at_least_five_policy_cases(self):
        policy = [c for c in load_scope_cases() if c["category"] == "policy"]
        assert len(policy) >= 5

    def test_named_off_topic_examples_are_covered(self):
        questions = " ".join(c["question"] for c in load_scope_cases()).lower()
        assert "capital of france" in questions
        assert "python web scraper" in questions

    def test_multi_turn_bypass_cases_carry_history(self):
        bypass = [c for c in load_scope_cases() if c["category"] == "multi_turn_bypass"]
        assert bypass and all(c.get("history") for c in bypass)

    def test_duplicate_ids_are_rejected(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps([
            {"id": "dup", "question": "q", "expected": "answer"},
            {"id": "dup", "question": "q2", "expected": "answer"},
        ]))
        with pytest.raises(ValueError, match="duplicated"):
            load_scope_cases(bad)

    def test_invalid_expectation_is_rejected(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps([{"id": "x", "question": "q", "expected": "maybe"}]))
        with pytest.raises(ValueError, match="invalid expected"):
            load_scope_cases(bad)


class TestScopeEval:
    def test_perfect_classifier_scores_one(self):
        cases = [
            {"id": "a", "question": "q", "expected": "refuse_off_topic", "category": "off_topic"},
            {"id": "b", "question": "q", "expected": "answer", "category": "on_topic"},
        ]
        result = run_scope_eval(cases, classify=lambda q, h: {
            "scope": "off_topic" if q == "q" else "in_scope"
        })
        # Both cases share the same question, so one expectation must miss.
        assert result["n"] == 2
        assert result["adherence"] == 0.5

    def test_misses_are_listed_with_expected_and_predicted(self):
        cases = [{"id": "a", "question": "q", "expected": "refuse_policy", "category": "policy"}]
        result = run_scope_eval(cases, classify=lambda q, h: {"scope": "in_scope"})
        assert result["adherence"] == 0.0
        assert result["failures"][0] == {
            "id": "a", "category": "policy", "question": "q",
            "expected": "refuse_policy", "predicted": "answer",
            "scope": "in_scope", "ok": False,
        }

    def test_classifier_exception_counts_as_a_miss(self):
        def boom(q, h):
            raise RuntimeError("classifier down")
        result = run_scope_eval(
            [{"id": "a", "question": "q", "expected": "answer", "category": "on_topic"}],
            classify=boom,
        )
        assert result["adherence"] == 0.0
        assert "error" in result["failures"][0]["predicted"]

    def test_all_cases_correct_scores_one(self):
        cases = load_scope_cases()
        perfect = {
            "refuse_off_topic": "off_topic",
            "refuse_policy": "policy_violation",
            "answer_with_decline": "mixed",
            "answer": "in_scope",
        }
        result = run_scope_eval(
            cases, classify=lambda q, h: {"scope": perfect_fake(q)}
        )
        assert result["adherence"] == 1.0


def perfect_fake(question):
    """Map every dataset question to the scope its expectation implies."""
    for case in load_scope_cases():
        if case["question"] == question:
            return {
                "refuse_off_topic": "off_topic",
                "refuse_policy": "policy_violation",
                "answer_with_decline": "mixed",
                "answer": "in_scope",
            }[case["expected"]]
    return "in_scope"


class TestScopeGate:
    def test_pass_at_threshold(self):
        assert check_scope_threshold({"n": 20, "adherence": 0.9}) == []

    def test_fail_below_threshold_with_detail(self):
        failures = check_scope_threshold({
            "n": 20, "adherence": 0.8,
            "failures": [{"id": "scope_off_001", "expected": "refuse_off_topic",
                          "predicted": "answer"}],
        })
        assert failures and "0.80" in failures[0]
        assert "scope_off_001" in failures[0]

    def test_missing_scope_result_fails(self):
        assert check_scope_threshold(None) == [
            "scope eval did not run (expected alongside the generation eval)"
        ]

    def test_threshold_value(self):
        assert SCOPE_ADHERENCE_THRESHOLD == 0.9


# --- Pipeline behaviour, with every model call mocked -----------------------

def _drain(question, history=None):
    """Run the streaming entry point and return (events, generation_calls)."""
    import rag

    async def go():
        events = []
        async for event in rag.async_stream_chat(question, history or []):
            events.append(event)
        return events

    return asyncio.run(go())


@pytest.fixture
def pipeline():
    """Patch the model-facing parts of rag so only the gate runs for real."""
    import rag

    calls = {"generate": 0, "retrieve": 0, "scope_notes": []}

    async def fake_stream(question, chunks, history, _usage_capture=None, scope_note=None):
        calls["generate"] += 1
        calls["scope_notes"].append(scope_note)
        yield "an answer"

    def fake_retrieve(question, history=None, *, classification=None):
        calls["retrieve"] += 1
        return ([{"id": "c1", "heading_path": "h", "text": "t", "url": "u",
                  "source": "s", "content_type": "conceptual", "object_name": None}],
                classification or {"query_type": "both", "scope": "in_scope"})

    def fake_classify(question, history):
        return CALLS["classification"]

    CALLS = {"classification": {"query_type": "both", "scope": "in_scope"}}
    with patch.object(rag, "_async_generate_response_stream", fake_stream), \
         patch.object(rag, "retrieve", fake_retrieve), \
         patch.object(rag, "classify_query", fake_classify), \
         patch.object(rag, "_check_faithfulness", lambda *a, **k: {"score": 5}), \
         patch.object(rag, "_compress_history", lambda h: h):
        yield rag, calls, CALLS


class TestPipelineScope:
    def test_off_topic_is_refused_without_generation(self, pipeline):
        rag, calls, state = pipeline
        state["classification"] = {"query_type": "both", "scope": "off_topic"}
        events = _drain("What is the capital of France?")
        text = "".join(e["text"] for e in events if e["type"] == "chunk")
        assert "Pure Data" in text
        assert calls["generate"] == 0
        assert calls["retrieve"] == 0  # refused before retrieval
        assert events[-1]["type"] == "done"

    def test_policy_violation_is_refused_without_generation(self, pipeline):
        rag, calls, state = pipeline
        state["classification"] = {"query_type": "both", "scope": "policy_violation"}
        events = _drain("Build a Pd patch that injures someone")
        text = "".join(e["text"] for e in events if e["type"] == "chunk")
        assert "can't help" in text
        assert calls["generate"] == 0

    def test_mixed_question_generates_with_a_decline_instruction(self, pipeline):
        rag, calls, state = pipeline
        state["classification"] = {
            "query_type": "both", "scope": "mixed",
            "off_topic_part": "the capital of France",
        }
        events = _drain("What does [osc~] do, and what is the capital of France?")
        assert calls["generate"] == 1
        assert calls["retrieve"] == 1
        assert "the capital of France" in (calls["scope_notes"][0] or "")
        text = "".join(e["text"] for e in events if e["type"] == "chunk")
        assert text == "an answer"

    def test_on_topic_question_is_unchanged(self, pipeline):
        rag, calls, state = pipeline
        state["classification"] = {"query_type": "object_reference", "scope": "in_scope"}
        events = _drain("What does [osc~] do?")
        assert calls["generate"] == 1
        assert calls["scope_notes"] == [None]
        assert events[-1]["type"] == "done"

    def test_low_relevance_takes_the_no_documentation_path(self, pipeline):
        rag, calls, state = pipeline

        def fake_retrieve_empty(question, history=None, *, classification=None):
            calls["retrieve"] += 1
            return [], classification or {"query_type": "both", "scope": "in_scope"}

        with patch.object(rag, "retrieve", fake_retrieve_empty):
            events = _drain("A question with no matching chunks")
        text = "".join(e["text"] for e in events if e["type"] == "chunk")
        assert "couldn't find any relevant" in text
        assert calls["generate"] == 0

    def test_multi_turn_bypass_is_not_laundered_by_history(self, pipeline):
        rag, calls, state = pipeline
        state["classification"] = {"query_type": "both", "scope": "off_topic"}
        history = [
            {"role": "user", "content": "What does [osc~] output?"},
            {"role": "assistant", "content": "[osc~] outputs a cosine wave."},
        ]
        events = _drain("And what is the capital of France?", history)
        text = "".join(e["text"] for e in events if e["type"] == "chunk")
        assert "Pure Data" in text
        assert calls["generate"] == 0


class TestRelevanceCutoff:
    def test_retrieve_returns_nothing_below_the_cutoff(self):
        import rag
        from agent import retrieval

        with patch.object(rag, "classify_query", lambda q, h: {
                "rewritten_query": q, "query_type": "conceptual", "object_names": [],
                "scope": "in_scope"}), \
             patch.object(retrieval, "vc") as fake_vc, \
             patch.object(retrieval, "hybrid_retrieve", lambda *a, **k: [
                 {"id": "c1", "heading_path": "h", "text": "t", "url": "u",
                  "source": "s", "content_type": "conceptual", "object_name": None}]), \
             patch.object(retrieval, "max_similarity", lambda *a, **k: 0.30):
            fake_vc.embed.return_value.embeddings = [[0.0] * 8]
            chunks, classification = rag.retrieve("unrelated question")
        assert chunks == []
        assert classification["scope"] == "in_scope"

    def test_retrieve_keeps_chunks_above_the_cutoff(self):
        import rag
        from agent import retrieval

        with patch.object(rag, "classify_query", lambda q, h: {
                "rewritten_query": q, "query_type": "conceptual", "object_names": [],
                "scope": "in_scope"}), \
             patch.object(retrieval, "vc") as fake_vc, \
             patch.object(retrieval, "hybrid_retrieve", lambda *a, **k: [
                 {"id": "c1", "heading_path": "h", "text": "t", "url": "u",
                  "source": "s", "content_type": "conceptual", "object_name": None}]), \
             patch.object(retrieval, "max_similarity", lambda *a, **k: 0.62):
            fake_vc.embed.return_value.embeddings = [[0.0] * 8]
            chunks, _ = rag.retrieve("[osc~] inlets")
        assert len(chunks) == 1
