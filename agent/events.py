"""The typed event stream produced by the agent core.

The loop and the request orchestration in this package never import a web
framework, an SSE helper or a provider SDK's event types. They yield the
dataclasses below; each interface (SSE, CLI, Slack, MCP) maps them onto its own
wire format. That boundary is what keeps the core transport-independent, and it
is what makes the loop testable without a server.

Event order for a normal request::

    model_turn -> tool_call -> tool_result -> sources -> text_delta* -> model_turn -> artifact* -> done

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
class ModelTurnEvent:
    """One completed model turn, with its raw blocks, token usage, cost and latency."""

    type: ClassVar[str] = "model_turn"
    turn: int
    model: str
    content: tuple[dict[str, Any], ...] = ()
    text: str = ""
    stop_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    cost_usd: float | None = None
    latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "turn": self.turn,
            "model": self.model,
            "content": list(self.content),
            "text": self.text,
            "stop_reason": self.stop_reason,
            "usage": dict(self.usage),
            "cost_usd": self.cost_usd,
            "latency_ms": self.latency_ms,
        }

    def to_log_dict(self) -> dict[str, Any]:
        return self.to_dict()


@dataclass(frozen=True)
class TextDelta:
    """A piece of the answer as the model produced it."""

    type: ClassVar[str] = "text_delta"
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "text": self.text}

    def to_log_dict(self) -> dict[str, Any]:
        return self.to_dict()


@dataclass(frozen=True)
class TextReset:
    """Discard all answer text so far: it was written before a tool call."""

    type: ClassVar[str] = "text_reset"

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type}

    def to_log_dict(self) -> dict[str, Any]:
        return self.to_dict()


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

    def to_log_dict(self) -> dict[str, Any]:
        return self.to_dict()


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
    latency_ms: float | None = None

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
            **({"latency_ms": self.latency_ms} if self.latency_ms is not None else {}),
        }

    def to_log_dict(self) -> dict[str, Any]:
        # The run log keeps the full tool output and retrieved chunks so runs
        # can be replayed, debugged and resumed without repeating tool calls.
        return {
            **self.to_dict(),
            "content": self.content,
            "chunks": list(self.chunks),
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

    def to_log_dict(self) -> dict[str, Any]:
        return self.to_dict()


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

    def to_log_dict(self) -> dict[str, Any]:
        return self.to_dict()


@dataclass(frozen=True)
class ApprovalRequested:
    """A durable run paused waiting for human approval before executing a tool."""

    type: ClassVar[str] = "approval_requested"
    call_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    interface: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "reason": self.reason,
            "interface": self.interface,
        }

    def to_log_dict(self) -> dict[str, Any]:
        return self.to_dict()


@dataclass(frozen=True)
class ApprovalResolved:
    """Human approval decision received (or timed out) for a paused tool call."""

    type: ClassVar[str] = "approval_resolved"
    call_id: str
    tool_name: str
    approved: bool
    comment: str = ""
    timed_out: bool = False
    #: Who decided (TASK-5.30); empty when the request timed out.
    approver: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "approved": self.approved,
            "comment": self.comment,
            "timed_out": self.timed_out,
            "approver": self.approver,
        }

    def to_log_dict(self) -> dict[str, Any]:
        return self.to_dict()


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
    budget_exceeded: str | None = None
    patches: tuple[dict[str, Any], ...] = ()
    #: ``{"name", "version"}`` for every skill this run loaded, so the run log
    #: records which version of each skill shaped the answer.
    skills: tuple[dict[str, str], ...] = ()
    run_id: str | None = None
    session_id: str | None = None
    langfuse_trace_id: str | None = None
    langfuse_trace_url: str | None = None

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
            **({"budget_exceeded": self.budget_exceeded} if self.budget_exceeded else {}),
            **({"skills": list(self.skills)} if self.skills else {}),
            **({"run_id": self.run_id} if self.run_id else {}),
            **({"session_id": self.session_id} if self.session_id else {}),
            **({"langfuse_trace_id": self.langfuse_trace_id} if self.langfuse_trace_id else {}),
            **({"langfuse_trace_url": self.langfuse_trace_url} if self.langfuse_trace_url else {}),
        }

    def to_log_dict(self) -> dict[str, Any]:
        return {
            **self.to_dict(),
            "answer": self.answer,
            "query_type": self.query_type,
            "patches": list(self.patches),
            "skills": list(self.skills),
            "chunk_count": len(self.chunks),
        }


AgentEvent = (
    ModelTurnEvent
    | TextDelta
    | TextReset
    | ToolCall
    | ToolResult
    | Sources
    | Artifact
    | ApprovalRequested
    | ApprovalResolved
    | Done
)
