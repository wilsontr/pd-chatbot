"""Extract the text of an Anthropic Messages API response.

Current Claude models (and other providers' reasoning models) can return more
than one content block, and a reasoning/thinking block may come *first*:

    [ThinkingBlock(...), TextBlock(text="...")]

Indexing ``response.content[0].text`` therefore raises ``AttributeError`` on
those models — it only works while the first block happens to be text. This
helper concatenates the text blocks and ignores the rest, so the same code path
works for plain models (one text block) and thinking models (thinking + text).

Deliberately dependency-free: the agent core (agent/loop.py, via the streaming
client), the pipeline baseline (rag.py) and the evaluation harness
(evaluation/generation.py, evaluation/judge.py) all use it.
"""

from __future__ import annotations

from typing import Any


def text_of(response: Any) -> str:
    """Return the concatenated text blocks of a Messages API response.

    Raises ValueError when the response contains no text block at all, which is
    a clearer failure than an ``AttributeError`` deep inside a caller.
    """
    blocks = getattr(response, "content", None) or []
    parts: list[str] = []
    for block in blocks:
        # Real SDK blocks expose .type; tolerate test doubles that only carry .text.
        block_type = getattr(block, "type", None)
        text = getattr(block, "text", None)
        if text is None:
            continue
        if block_type is not None and block_type != "text":
            continue
        parts.append(text)
    if not parts:
        types = [getattr(block, "type", type(block).__name__) for block in blocks]
        raise ValueError(f"response contained no text block (blocks: {types})")
    return "".join(parts)
