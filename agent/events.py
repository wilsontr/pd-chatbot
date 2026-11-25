"""The typed event stream produced by the agent core.

The loop and the request orchestration in this package never import a web
framework, an SSE helper or a provider SDK's event types. They yield the
dataclasses below; each interface (SSE, CLI, Slack, MCP) maps them onto its own
wire format. That boundary is what keeps the core transport-independent, and it
is what makes the loop testable without a server.

Event order for a normal request::

    tool_call -> tool_result -> sources -> text_delta* -> artifact* -> done

``text_reset`` may follow a ``text_delta``: the model wrote text and then called a
tool, so adapters discard all answer text so far.

``sources`` is always emitted before the first ``text_delta``, because every
adapter needs to attach citations to the answer before it starts showing it.
``done`` is always last and carries the final answer and the new history.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

from .types import Chunk, HistoryItem, Source


@dataclass(frozen=True)
class TextDelta:
    """A piece of the answer as the model produced it."""

    type: ClassVar[str] = "text_delta"
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "text": self.text}


@dataclass(frozen=True)
class TextReset:
    """Discard all answer text so far: it was written before a tool call."""

    type: ClassVar[str] = "text_reset"

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type}


@dataclass(frozen=True)
class ToolCall:
    """The model asked to run one tool."""

    type: ClassVar[str] = "tool_call"
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "id": self.id,
            "name": self.name,
            "arguments": self.arguments,
        }


@dataclass(frozen=True)
class ToolResult:
    """The tool's reply, as sent back to the model and summarised for the UI."""

    type: ClassVar[str] = "tool_result"
    id: str
    name: str
    ok: bool
    summary: str
    content: str = ""
    chunks: tuple[Chunk, ...] = ()
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        # ``content`` is the full text the model reads (potentially thousands of
        # tokens of excerpts); adapters get a summary plus the structured data.
        return {
            "type": self.type,
            "id": self.id,
            "name": self.name,
            "ok": self.ok,
            "summary": self.summary,
            "chunk_count": len(self.chunks),
            "data": self.data,
        }


@dataclass(frozen=True)
class Sources:
    """Citations for the answer. Emitted once, before the first text delta."""

    type: ClassVar[str] = "sources"
    items: tuple[Source, ...] = ()
    query_type: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "sources": list(self.items),
            "query_type": self.query_type,
        }


@dataclass(frozen=True)
class Artifact:
    """A structured output of the run.

    ``pd_patch``: ``{"name", "patch"}``. ``audio``: ``{"name", "id", "url", "mime",
    "seconds"}``, where ``name`` is the patch that made it and the bytes are in
    ``agent.artifacts`` under ``id``.
    """

    type: ClassVar[str] = "artifact"
    kind: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "kind": self.kind, "data": self.data}


@dataclass(frozen=True)
class Done:
    """Terminal event: the finished answer and the history to send next turn."""

    type: ClassVar[str] = "done"
    answer: str
    new_history: list[HistoryItem]
    chunks: tuple[Chunk, ...] = ()
    query_type: str = ""
    turns: int = 0
    tool_calls: int = 0
    refused: bool = False
    cached: bool = False
    patch_failed: bool = False
    patches: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        # The SSE adapter historically sent only {"type", "history"}; keep the
        # shape additive so older clients keep working.
        return {
            "type": self.type,
            "history": self.new_history,
            "turns": self.turns,
            "tool_calls": self.tool_calls,
            "refused": self.refused,
            "cached": self.cached,
            "patch_failed": self.patch_failed,
        }


AgentEvent = TextDelta | TextReset | ToolCall | ToolResult | Sources | Artifact | Done
