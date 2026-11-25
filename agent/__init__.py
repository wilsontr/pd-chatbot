"""Transport-independent agent core.

Everything a request needs, with no web framework, no SSE and no provider SDK
event types leaking out:

=================  ==========================================================
``agent.loop``     the hand-written tool-use loop; yields typed events
``agent.tools``    the tool contract and registry (``tools.docs`` = search_docs)
``agent.chat``     request orchestration: cache, scope gate, guardrails, artifacts
``agent.sessions`` the patches stored per conversation, for follow-up edits
``agent.events``   the typed event stream every interface adapts
=================  ==========================================================

Importing the event types is cheap (no Chroma index, no API client); the heavier
entry points are resolved lazily, so ``import agent`` in a test or a CLI stays
fast and key-free.
"""

from __future__ import annotations

from importlib import import_module

from dotenv import load_dotenv

# This is an application package, not a reusable library: every entry point
# (web server, eval harness, CLI) needs the same provider keys, and this is the
# one import they all perform. ``load_dotenv`` does not override real env vars,
# so CI and production keep their injected configuration.
load_dotenv()

from .events import (
    AgentEvent,
    ApprovalRequested,
    ApprovalResolved,
    Artifact,
    Done,
    ModelTurnEvent,
    Sources,
    TextDelta,
    TextReset,
    ToolCall,
    ToolResult,
)
from .types import Chunk, HistoryItem, Source

_LAZY: dict[str, str] = {
    # agent.chat
    "AgentAnswer": "agent.chat",
    "answer_question": "agent.chat",
    "stream_chat": "agent.chat",
    # agent.durable
    "DurableRuntime": "agent.durable",
    "approve_run": "agent.durable",
    "destroy_dbos": "agent.durable",
    "get_durable_run_result": "agent.durable",
    "get_pending_approval": "agent.durable",
    "launch_dbos": "agent.durable",
    "reset_runtime": "agent.durable",
    "set_runtime": "agent.durable",
    "start_durable_run": "agent.durable",
    # agent.guardrails
    "INTERFACE_POLICIES": "agent.guardrails",
    "ToolPolicy": "agent.guardrails",
    "policy_for": "agent.guardrails",
    # agent.loop
    "AgentConfig": "agent.loop",
    "AgentLoop": "agent.loop",
    "LoopState": "agent.loop",
    # agent.sessions
    "SessionPatchStore": "agent.sessions",
    # agent.store
    "RunRecorder": "agent.store",
    "RunStore": "agent.store",
    # agent.tools
    "ToolOutcome": "agent.tools",
    "ToolRegistry": "agent.tools",
    "ToolSpec": "agent.tools",
    "SearchDocsTool": "agent.tools.docs",
    "default_tools": "agent.tools.docs",
}


def __getattr__(name: str):
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(target), name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY))


__all__ = [
    "AgentAnswer",
    "AgentConfig",
    "AgentEvent",
    "AgentLoop",
    "ApprovalRequested",
    "ApprovalResolved",
    "Artifact",
    "Chunk",
    "Done",
    "DurableRuntime",
    "HistoryItem",
    "LoopState",
    "ModelTurnEvent",
    "RunRecorder",
    "RunStore",
    "SearchDocsTool",
    "SessionPatchStore",
    "Source",
    "Sources",
    "TextDelta",
    "TextReset",
    "ToolCall",
    "ToolOutcome",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "answer_question",
    "approve_run",
    "default_tools",
    "destroy_dbos",
    "get_durable_run_result",
    "get_pending_approval",
    "launch_dbos",
    "reset_runtime",
    "set_runtime",
    "start_durable_run",
    "stream_chat",
]
