"""Tests for the search_docs tool contract (TASK-5.1).

The tool is what the model sees of the retrieval system, so its argument
handling and its "nothing matched" message are part of the contract: a bad
argument must tell the model how to recover, and a miss must push it away from
inventing a source rather than toward it.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agent.retrieval import RetrievalOutcome
from agent.tools.docs import SearchDocsTool
from agent.tools import ToolRegistry

CHUNK = {
    "id": "parent-1",
    "heading_path": "osc~",
    "text": "osc~ is a cosine oscillator.",
    "url": "https://example.test/osc~",
    "source": "iem_reference",
    "content_type": "object_reference",
    "object_name": "osc~",
}


@pytest.fixture
def calls():
    seen: list[dict] = []

    def fake_search(query, **kwargs):
        seen.append({"query": query, **kwargs})
        return RetrievalOutcome(
            chunks=[CHUNK],
            query=query,
            best_similarity=0.7,
        )

    with patch("agent.tools.docs.search_documents", fake_search):
        yield seen


class TestArguments:
    def test_empty_query_is_rejected_with_recovery_instructions(self):
        tool = SearchDocsTool()
        with patch("agent.tools.docs.search_documents") as search:
            outcome = tool.run({"query": "   "})
        search.assert_not_called()
        assert outcome.ok is False
        assert "non-empty 'query'" in outcome.content

    def test_query_is_passed_through_with_the_selected_corpus(self, calls):
        tool = SearchDocsTool()
        outcome = tool.run({
            "query": "band-pass filter",
            "content_type": "conceptual",
            "object_names": ["biquad~"],
        })
        assert calls == [{
            "query": "band-pass filter",
            "top_k": 5,
            "content_type": "conceptual",
            "object_names": ["biquad~"],
        }]
        assert outcome.ok is True

    def test_both_means_no_corpus_filter(self, calls):
        SearchDocsTool().run({"query": "osc~", "content_type": "both"})
        assert calls[0]["content_type"] is None

    def test_unknown_content_type_falls_back_to_everything(self, calls):
        SearchDocsTool().run({"query": "osc~", "content_type": "nonsense"})
        assert calls[0]["content_type"] is None

    def test_object_names_are_cleaned_and_deduplicated(self, calls):
        SearchDocsTool().run({
            "query": "osc~",
            "object_names": [" osc~ ", "osc~", "", 42],
        })
        assert calls[0]["object_names"] == ["osc~", "42"]

    def test_object_names_omitted_is_none(self, calls):
        SearchDocsTool().run({"query": "osc~"})
        assert calls[0]["object_names"] is None

    def test_top_k_is_configurable(self, calls):
        SearchDocsTool(top_k=3).run({"query": "osc~"})
        assert calls[0]["top_k"] == 3


class TestOutcome:
    def test_hit_returns_citable_excerpts_and_chunks(self, calls):
        outcome = SearchDocsTool().run({"query": "osc~ inlets"})
        assert "Source: https://example.test/osc~" in outcome.content
        assert "osc~ is a cosine oscillator." in outcome.content
        assert outcome.chunks == (CHUNK,)
        assert outcome.summary == "1 sections for 'osc~ inlets'"
        assert outcome.data["matched"] == 1

    def test_miss_tells_the_model_not_to_invent_a_source(self):
        tool = SearchDocsTool()
        with patch("agent.tools.docs.search_documents",
                   lambda *a, **k: RetrievalOutcome(chunks=[], query="xyz")):
            outcome = tool.run({"query": "xyz"})
        assert outcome.ok is True  # a miss is a useful result, not an error
        assert outcome.chunks == ()
        assert "Do not invent a source URL" in outcome.content
        assert outcome.data["matched"] == 0

    def test_tool_is_registered_with_a_model_facing_schema(self):
        registry = ToolRegistry([SearchDocsTool()])
        specs = registry.specs()
        assert len(specs) == 1
        spec = specs[0]
        assert spec["name"] == "search_docs"
        assert spec["input_schema"]["required"] == ["query"]
        assert "query" in spec["input_schema"]["properties"]
        assert registry.get("search_docs") is not None
        assert "search_docs" in registry
