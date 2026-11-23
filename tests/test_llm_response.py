"""Tests for llm_response.text_of (multi-block Messages API responses)."""

from types import SimpleNamespace

import pytest

from llm_response import text_of


def _block(kind, **kwargs):
    return SimpleNamespace(type=kind, **kwargs)


class TestTextOf:
    def test_single_text_block(self):
        response = SimpleNamespace(content=[_block("text", text="hello")])
        assert text_of(response) == "hello"

    def test_thinking_block_before_text_is_skipped(self):
        # claude-sonnet-5-5 / haiku-5-5 return this shape by default; indexing
        # content[0].text used to raise AttributeError on it.
        response = SimpleNamespace(content=[
            _block("thinking", thinking="hmm", signature="sig"),
            _block("text", text="the answer"),
        ])
        assert text_of(response) == "the answer"

    def test_multiple_text_blocks_are_concatenated(self):
        response = SimpleNamespace(content=[
            _block("text", text="part one "),
            _block("thinking", thinking="..."),
            _block("text", text="part two"),
        ])
        assert text_of(response) == "part one part two"

    def test_no_text_block_raises_a_clear_error(self):
        response = SimpleNamespace(content=[_block("thinking", thinking="...")])
        with pytest.raises(ValueError, match="no text block"):
            text_of(response)

    def test_empty_content_raises(self):
        with pytest.raises(ValueError, match="no text block"):
            text_of(SimpleNamespace(content=[]))

    def test_tolerates_blocks_without_a_type(self):
        # Test doubles and older SDK objects may only carry .text.
        response = SimpleNamespace(content=[SimpleNamespace(text="plain")])
        assert text_of(response) == "plain"

    def test_missing_content_attribute_raises_cleanly(self):
        with pytest.raises(ValueError, match="no text block"):
            text_of(SimpleNamespace())
