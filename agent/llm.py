"""The model boundary of the agent core.

The loop talks to a ``ModelClient``, not to the Anthropic SDK. That is what lets
the loop be unit-tested with a stub that scripts turns (see
``tests/test_agent_loop.py``) and what would let another provider be swapped in
without touching the loop.

A client exposes one method, ``stream_turn(request)``, an async iterator that
yields ``ModelDelta`` for each piece of streamed text and exactly one final
``ModelTurn`` carrying the assistant content blocks (including tool_use), the
stop reason and token usage.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol

import anthropic
import newrelic.agent

from model_config import max_tokens_for, thinking_kwargs

logger = logging.getLogger(__name__)

_sync_client: anthropic.Anthropic | None = None
_async_client: anthropic.AsyncAnthropic | None = None


def sync_client() -> anthropic.Anthropic:
    """Shared synchronous client (classification, history compression, judges).

    Created lazily so importing the agent core never requires an API key — the
    unit tests import these modules with no credentials at all.
    """
    global _sync_client
    if _sync_client is None:
        _sync_client = anthropic.Anthropic()
    return _sync_client


def async_client() -> anthropic.AsyncAnthropic:
    global _async_client
    if _async_client is None:
        _async_client = anthropic.AsyncAnthropic()
    return _async_client


@dataclass(frozen=True)
class ToolCallRequest:
    """One tool_use block from the model."""

    id: str
    name: str
    input: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelRequest:
    """Everything one turn needs, provider-neutral."""

    model: str
    system: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] = field(default_factory=list)
    tool_choice: dict[str, Any] | None = None
    max_tokens: int | None = None

    def api_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens or max_tokens_for(self.model),
            "system": [{
                "type": "text",
                "text": self.system,
                "cache_control": {"type": "ephemeral"},
            }],
            "messages": self.messages,
            **thinking_kwargs(self.model),
        }
        if self.tools:
            kwargs["tools"] = self.tools
        if self.tool_choice is not None:
            kwargs["tool_choice"] = self.tool_choice
        return kwargs


@dataclass(frozen=True)
class ModelDelta:
    """A streamed piece of assistant text."""

    text: str


@dataclass(frozen=True)
class ModelTurn:
    """A completed assistant turn."""

    content: list[dict[str, Any]]
    text: str
    tool_calls: tuple[ToolCallRequest, ...] = ()
    stop_reason: str | None = None
    usage: dict[str, int] | None = None


class ModelClient(Protocol):
    def stream_turn(self, request: ModelRequest) -> AsyncIterator[ModelDelta | ModelTurn]:
        ...


def _block_to_dict(block: Any) -> dict[str, Any]:
    """Serialise an SDK content block for echoing back in the assistant turn."""
    dump = getattr(block, "model_dump", None)
    if callable(dump):
        return dump(exclude_none=True)
    return dict(block)


class AnthropicModelClient:
    """Streaming Messages API client.

    Text is streamed as it arrives; tool_use blocks arrive on the final message
    with their JSON already parsed, which is what the loop needs to run them.
    """

    def __init__(self, client: anthropic.AsyncAnthropic | None = None):
        self._client = client

    @property
    def client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            self._client = async_client()
        return self._client

    async def stream_turn(
        self, request: ModelRequest
    ) -> AsyncIterator[ModelDelta | ModelTurn]:
        text_parts: list[str] = []
        with newrelic.agent.ExternalTrace('anthropic', 'api.anthropic.com', 'POST'):
            async with self.client.messages.stream(**request.api_kwargs()) as stream:
                async for text in stream.text_stream:
                    if not text:
                        continue
                    text_parts.append(text)
                    yield ModelDelta(text)
                final = await stream.get_final_message()

        content = [_block_to_dict(block) for block in final.content]
        tool_calls = tuple(
            ToolCallRequest(
                id=block.get("id", ""),
                name=block.get("name", ""),
                input=block.get("input") or {},
            )
            for block in content
            if block.get("type") == "tool_use"
        )
        yield ModelTurn(
            content=content,
            text="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=getattr(final, "stop_reason", None),
            usage=_usage_dict(getattr(final, "usage", None)),
        )


def _usage_dict(usage: Any) -> dict[str, int] | None:
    if usage is None:
        return None
    try:
        return {
            "input": usage.input_tokens,
            "output": usage.output_tokens,
            "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0,
            "total": usage.input_tokens + usage.output_tokens,
        }
    except Exception:  # pragma: no cover - defensive, usage shape is SDK-owned
        logger.debug("could not read usage from model response", exc_info=True)
        return None
