"""The agent loop: model turns, tool calls, and the event stream between them.

Hand-written over the ``ModelClient`` boundary rather than a framework, so the
control flow that matters (when to call a tool, what the model sees back, when to
stop) is explicit and unit-testable with a scripted stub.

The loop owns one invariant that the rest of the system depends on: ``sources``
is yielded before the first ``text_delta``. Text the model emits before it calls
a tool is buffered and released right after the citations, so every adapter can
create the answer — with its sources — and then append text to it. Once the
citations are out, deltas are yielded as they arrive; if that turn then calls a
tool whose result could change the answer, a ``text_reset`` takes the text back and
the model is told to write its answer again. Text beside an output-only call
(``write_patch``) stays.

Bounded by ``max_turns``. If the budget runs out while the model still wants to
call tools, the loop asks once more with tools disabled and returns that answer
rather than leaving the user with nothing.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Mapping

import newrelic.agent

from model_config import ANSWER_MODEL

from . import observability
from .events import AgentEvent, Sources, TextDelta, TextReset, ToolCall, ToolResult
from .llm import ModelClient, ModelDelta, ModelRequest, ModelTurn
from .prompt import AGENT_SYSTEM_PROMPT
from .retrieval import chunks_to_sources
from .tools import ToolOutcome, ToolRegistry
from .types import Chunk, HistoryItem

logger = logging.getLogger(__name__)

TURN_LIMIT_NOTE = (
    "\n\nYou have reached the tool-call limit for this request. Answer now with "
    "the information you already have, and say plainly what you could not verify."
)


#: Sent with the tool results when an answer-length text was discarded.
DISCARDED_TEXT_NOTE = (
    "\n\n[The text you wrote before this tool call was not shown to the user. When you are "
    "done with tools, write the complete answer, including anything you had already written "
    "that still holds.]"
)
_NARRATION_CHARS = 200
_PATCH_TARGET_TOOLS = frozenset({"write_patch", "run_patch", "analyze_audio"})


def _is_tool_error(name: str, outcome: ToolOutcome) -> bool:
    """True when a tool call failed or surfaced a recoverable problem.

    Covers explicit tool failures (``ok=False``), empty ``search_docs``
    retrievals (which ask the model to broaden its query), and ``run_patch``
    runs where Pd reported errors or did not finish cleanly.
    """
    if not outcome.ok:
        return True
    if name == "search_docs" and not outcome.chunks:
        return True
    if name == "run_patch" and outcome.data.get("ran_cleanly") is False:
        return True
    return False


def _tool_error_key(name: str, arguments: Mapping[str, Any]) -> tuple[str, str]:
    """Identify the error episode so a later clean call can resolve it."""
    if name in _PATCH_TARGET_TOOLS:
        return (name, str(arguments.get("name") or ""))
    return (name, "")


@dataclass(frozen=True)
class AgentConfig:
    """Static settings for one run."""

    model: str = ANSWER_MODEL
    max_turns: int = 5
    system_prompt: str = AGENT_SYSTEM_PROMPT
    max_tokens: int | None = None
    #: "auto" (the model decides), or a forced choice for the first turn.
    tool_choice: dict[str, Any] | None = None
    force_final_answer: bool = True


@dataclass
class LoopState:
    """What the run accumulated. Read by the caller after the stream ends."""

    answer: str = ""
    chunks: list[Chunk] = field(default_factory=list)
    turns: int = 0
    tool_calls: int = 0
    retrieval_calls: int = 0
    tool_errors: int = 0
    recovered_errors: int = 0
    unresolved_errors: int = 0
    stop_reason: str | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    #: Token usage summed over every model turn in the run.
    usage: dict[str, int] = field(default_factory=dict)


class AgentLoop:
    """Run one question through the model with a tool registry."""

    def __init__(
        self,
        client: ModelClient,
        tools: ToolRegistry,
        config: AgentConfig | None = None,
    ):
        self.client = client
        self.tools = tools
        self.config = config or AgentConfig()
        self.state = LoopState()

    async def stream(
        self,
        question: str,
        *,
        history: list[HistoryItem] | None = None,
        scope_note: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Yield the run's events; ``self.state`` holds the result afterwards."""
        self.state = LoopState()
        user_content = question if not scope_note else f"{question}\n\n{scope_note}"
        messages: list[dict[str, Any]] = list(history or []) + [
            {"role": "user", "content": user_content}
        ]
        self.state.messages = messages

        answer_parts: list[str] = []
        pending: list[str] = []
        sources_sent = False
        turn = 0
        forcing_final = False
        retracted = ""
        separate = False
        open_errors: set[tuple[str, str]] = set()

        while True:
            if turn >= max(1, self.config.max_turns):
                if not self.config.force_final_answer or forcing_final:
                    break
                forcing_final = True

            request = ModelRequest(
                model=self.config.model,
                system=self.config.system_prompt + (TURN_LIMIT_NOTE if forcing_final else ""),
                # Snapshot: the request is a point-in-time view, and callers
                # (tests, tracing) must not see turns appended later.
                messages=list(messages),
                tools=self.tools.specs(),
                tool_choice=({"type": "none"} if forcing_final else self.config.tool_choice),
                max_tokens=self.config.max_tokens,
            )

            # Stream this turn inline: deltas must reach the client as they
            # arrive, and only a generator body can yield them.
            final: ModelTurn | None = None
            obs = observability.start_obs(
                "agent_turn", as_type="generation",
                model=self.config.model, metadata={"turn": turn + 1},
            )
            try:
                async for piece in self.client.stream_turn(request):
                    if isinstance(piece, ModelDelta):
                        if not piece.text:
                            continue
                        if separate:
                            # Keeps this turn's text apart from the kept turn's.
                            separate = False
                            (answer_parts if sources_sent else pending).append("\n\n")
                            if sources_sent:
                                yield TextDelta("\n\n")
                        if sources_sent:
                            # Citations are out: stream the answer as it arrives.
                            answer_parts.append(piece.text)
                            yield TextDelta(piece.text)
                        else:
                            # Held until we know whether this turn is narration
                            # for a tool call or the answer itself.
                            pending.append(piece.text)
                    else:
                        final = piece
            finally:
                self._record_turn(obs, final)

            if final is None:
                logger.error("model stream produced no final turn")
                self.state.stop_reason = "error"
                break

            messages.append({"role": "assistant", "content": final.content})
            self.state.turns = turn + 1
            self.state.stop_reason = final.stop_reason

            discarded = ""
            if final.tool_calls:
                if all(self._output_only(call.name) for call in final.tool_calls):
                    separate = bool("".join(answer_parts + pending).strip())
                else:
                    # Narration ("let me look that up"), or an answer written
                    # before its evidence. The tool events already give an
                    # interface its step display.
                    discarded = "".join(answer_parts + pending).strip()
                    if answer_parts:
                        answer_parts.clear()
                        yield TextReset()
                    pending.clear()
                    separate = False
                    if len(discarded) > _NARRATION_CHARS:
                        retracted = discarded

            if not final.tool_calls:
                if not "".join(answer_parts + pending).strip() and retracted.strip():
                    # The model wrote its answer before its last tool call and
                    # had nothing to add after it. Better that than no answer.
                    pending[:] = [retracted]
                    if sources_sent:
                        answer_parts.append(retracted)
                        yield TextDelta(retracted)
                        pending.clear()
                if not sources_sent:
                    sources_sent = True
                    async for event in self._citations_then_buffered(pending, answer_parts):
                        yield event
                self.state.answer = "".join(answer_parts)
                return

            if forcing_final:
                # Tools were disabled and the model still asked for one; stop.
                if not sources_sent:
                    async for event in self._citations_then_buffered(pending, answer_parts):
                        yield event
                self.state.answer = "".join(answer_parts)
                return

            result_blocks = []
            for call in final.tool_calls:
                yield ToolCall(id=call.id, name=call.name, arguments=dict(call.input))
                outcome = await self._run_tool(call.name, call.input)
                error_key = _tool_error_key(call.name, call.input)
                if _is_tool_error(call.name, outcome):
                    self.state.tool_errors += 1
                    open_errors.add(error_key)
                elif error_key in open_errors:
                    open_errors.remove(error_key)
                    self.state.recovered_errors += 1
                self.state.unresolved_errors = len(open_errors)
                if call.name == "search_docs" and outcome.chunks:
                    self.state.retrieval_calls += 1
                self.state.chunks.extend(outcome.chunks)
                self.state.tool_calls += 1
                yield ToolResult(
                    id=call.id,
                    name=call.name,
                    ok=outcome.ok,
                    summary=outcome.summary,
                    content=outcome.content,
                    chunks=outcome.chunks,
                    data=outcome.data,
                )
                result_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": outcome.content,
                    **({"is_error": True} if not outcome.ok else {}),
                })
            if len(discarded) > _NARRATION_CHARS:
                result_blocks[-1]["content"] += DISCARDED_TEXT_NOTE
            messages.append({"role": "user", "content": result_blocks})

            if not sources_sent and self.state.chunks:
                sources_sent = True
                yield Sources(items=tuple(chunks_to_sources(self.state.chunks)))

            turn += 1

        # Exhausted the loop without a text answer (error, or an uncooperative
        # final turn). Still emit the citations so the adapter has a valid stream.
        self.state.answer = "".join(answer_parts)
        if not sources_sent:
            async for event in self._citations_then_buffered(pending, answer_parts):
                yield event

    def _output_only(self, name: str) -> bool:
        tool = self.tools.get(name)
        return tool is not None and tool.spec.output_only

    async def _citations_then_buffered(
        self, pending: list[str], answer_parts: list[str] | None = None
    ) -> AsyncIterator[AgentEvent]:
        """Emit the citations, then release text held back until they were known."""
        yield Sources(items=tuple(chunks_to_sources(self.state.chunks)))
        for text in pending:
            if answer_parts is not None:
                answer_parts.append(text)
            yield TextDelta(text)
        pending.clear()

    def _record_turn(self, obs: Any, final: ModelTurn | None) -> None:
        """Close the turn's trace span and account its tokens."""
        if final is not None:
            try:
                obs.update(
                    output=final.text[:500],
                    usage_details=final.usage,
                    cost_details=observability.cost_from_usage(
                        self.config.model, final.usage
                    ),
                )
            except Exception:
                logger.debug("failed to record agent turn", exc_info=True)
            observability.record_model_call("agent_turn", self.config.model, final.usage)
            if final.usage:
                for key, value in final.usage.items():
                    self.state.usage[key] = self.state.usage.get(key, 0) + int(value or 0)
        obs.end()

    async def _run_tool(self, name: str, arguments: Mapping[str, Any]) -> ToolOutcome:
        """Run one tool off the event loop, converting failures into model-visible text."""
        tool = self.tools.get(name)
        if tool is None:
            available = ", ".join(self.tools.names()) or "none"
            return ToolOutcome(
                content=(
                    f"There is no tool named {name!r}. Available tools: {available}. "
                    "Answer without it or call an available tool."
                ),
                summary=f"unknown tool {name!r}",
                ok=False,
            )
        try:
            return await asyncio.to_thread(tool.run, dict(arguments))
        except Exception as exc:
            logger.warning("tool %s failed", name, exc_info=True)
            newrelic.agent.record_custom_metric("Custom/Agent/ToolError", 1)
            return ToolOutcome(
                content=(
                    f"The {name} tool failed with: {exc}. You can retry with different "
                    "arguments, or answer from what you already have and say what you "
                    "could not verify."
                ),
                summary=f"{name} failed",
                ok=False,
            )

