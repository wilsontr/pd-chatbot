"""Unit tests for the agent loop, driven by a scripted model client (TASK-5.1).

No API key, no Chroma index, no network: the loop only knows the ``ModelClient``
and ``ToolRegistry`` interfaces, so a stub client can script exactly the turns a
test needs (answer directly, call a tool then answer, fail, run out of budget).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent.events import Sources, TextDelta, ToolCall, ToolResult
from agent.llm import ModelDelta, ModelRequest, ModelTurn, ToolCallRequest
from agent.loop import AgentConfig, AgentLoop
from agent.tools import ToolOutcome, ToolRegistry, ToolSpec

CHUNK = {
    "id": "parent-1",
    "heading_path": "osc~",
    "text": "osc~ is a cosine oscillator.",
    "url": "https://example.test/osc~",
    "source": "iem_reference",
    "content_type": "object_reference",
    "object_name": "osc~",
}


# --- Stubs -------------------------------------------------------------------


class StubClient:
    """Scripted ModelClient: one scripted turn per call."""

    def __init__(self, turns: list[dict[str, Any]]):
        self._turns = list(turns)
        self.requests: list[ModelRequest] = []

    async def stream_turn(self, request: ModelRequest):
        self.requests.append(request)
        if not self._turns:
            raise AssertionError("the loop asked for more turns than the test scripted")
        script = self._turns.pop(0)
        if callable(script):
            script = script(request)
        for delta in script.get("deltas", []):
            yield ModelDelta(delta)
        yield _model_turn(script)


def _model_turn(script: dict[str, Any]) -> ModelTurn:
    content: list[dict[str, Any]] = []
    text = script.get("text", "")
    if text:
        content.append({"type": "text", "text": text})
    calls = []
    for raw in script.get("tool_calls", []):
        block = {
            "type": "tool_use",
            "id": raw["id"],
            "name": raw["name"],
            "input": raw.get("input", {}),
        }
        content.append(block)
        calls.append(
            ToolCallRequest(id=raw["id"], name=raw["name"], input=raw.get("input", {}))
        )
    return ModelTurn(
        content=content,
        text=text,
        tool_calls=tuple(calls),
        stop_reason="tool_use" if calls else "end_turn",
        usage={"input": 10, "output": 5, "total": 15},
    )


def answer(text: str) -> dict[str, Any]:
    return {"text": text, "deltas": [text]}


def tool_turn(*, name: str = "search_docs", args: dict | None = None,
              text: str = "", call_id: str = "call-1") -> dict[str, Any]:
    return {
        "text": text,
        "deltas": [text] if text else [],
        "tool_calls": [{"id": call_id, "name": name, "input": args or {"query": "osc~"}}],
    }


class StubTool:
    spec = ToolSpec(
        name="search_docs",
        description="stub retrieval",
        input_schema={"type": "object", "properties": {}},
    )

    def __init__(self, outcome: ToolOutcome | None = None, error: Exception | None = None):
        self.outcome = outcome or ToolOutcome(
            content="[IEM Object Reference: osc~]\nosc~ is a cosine oscillator.\n"
                    "Source: https://example.test/osc~",
            summary="1 section for 'osc~'",
            chunks=(CHUNK,),
        )
        self.error = error
        self.calls: list[dict] = []

    def run(self, arguments):
        self.calls.append(dict(arguments))
        if self.error is not None:
            raise self.error
        return self.outcome


def registry(tool: StubTool) -> ToolRegistry:
    return ToolRegistry([tool])


def run(loop: AgentLoop, question: str = "What is osc~?") -> list[Any]:
    async def drain():
        return [event async for event in loop.stream(question)]

    return asyncio.run(drain())


def kinds(events: list[Any]) -> list[str]:
    return [type(event).__name__ for event in events]


# --- Tests -------------------------------------------------------------------


class TestSingleTurn:
    def test_answers_without_a_tool_and_still_emits_sources_first(self):
        client = StubClient([answer("Pure Data is a visual language.")])
        loop = AgentLoop(client, registry(StubTool()))

        events = run(loop)

        assert kinds(events) == ["Sources", "TextDelta", "ModelTurnEvent"]
        assert events[0].items == ()
        assert events[1].text == "Pure Data is a visual language."
        assert events[2].turn == 1
        assert loop.state.answer == "Pure Data is a visual language."
        assert loop.state.turns == 1
        assert loop.state.tool_calls == 0

    def test_deltas_are_forwarded_in_order(self):
        client = StubClient([{"text": "one two", "deltas": ["one ", "two"]}])
        loop = AgentLoop(client, registry(StubTool()))

        events = run(loop)

        assert [e.text for e in events if isinstance(e, TextDelta)] == ["one ", "two"]
        assert loop.state.answer == "one two"

    def test_request_carries_the_agent_prompt_and_tool_schema(self):
        client = StubClient([answer("ok")])
        tool = StubTool()
        loop = AgentLoop(client, registry(tool))

        run(loop)

        request = client.requests[0]
        assert request.tools and request.tools[0]["name"] == "search_docs"
        assert "search_docs" in request.system
        assert request.tool_choice is None
        # Prompt caching: the stable system prompt is marked cacheable.
        assert request.api_kwargs()["system"][0]["cache_control"] == {"type": "ephemeral"}


class TestToolUse:
    def test_tool_call_then_answer(self):
        client = StubClient([
            tool_turn(args={"query": "osc~ inlets", "object_names": ["osc~"]}),
            answer("It has two inlets."),
        ])
        tool = StubTool()
        loop = AgentLoop(client, registry(tool))

        events = run(loop)

        assert kinds(events) == [
            "ModelTurnEvent", "ToolCall", "ToolResult", "Sources", "TextDelta", "ModelTurnEvent"
        ]
        call = events[1]
        assert isinstance(call, ToolCall)
        assert call.name == "search_docs"
        assert call.arguments == {"query": "osc~ inlets", "object_names": ["osc~"]}
        result = events[2]
        assert isinstance(result, ToolResult)
        assert result.ok and result.chunks == (CHUNK,)
        assert result.latency_ms is not None and result.latency_ms >= 0
        assert events[3].items[0]["heading_path"] == "osc~"
        assert loop.state.retrieval_calls == 1
        assert loop.state.tool_calls == 1
        assert loop.state.chunks == [CHUNK]

    def test_tool_arguments_reach_the_tool(self):
        tool = StubTool()
        client = StubClient([tool_turn(args={"query": "band-pass"}), answer("done")])

        run(AgentLoop(client, registry(tool)))

        assert tool.calls == [{"query": "band-pass"}]

    def test_tool_result_is_sent_back_to_the_model(self):
        client = StubClient([tool_turn(call_id="abc"), answer("done")])
        loop = AgentLoop(client, registry(StubTool()))

        run(loop)

        follow_up = client.requests[1].messages
        assert follow_up[-2]["role"] == "assistant"
        assert follow_up[-2]["content"][0]["type"] == "tool_use"
        assert follow_up[-1]["role"] == "user"
        block = follow_up[-1]["content"][0]
        assert block["type"] == "tool_result"
        assert block["tool_use_id"] == "abc"
        assert "cosine oscillator" in block["content"]

    def test_narration_before_the_tool_call_is_not_glued_to_the_answer(self):
        """Text emitted with a tool_use block is a step, not answer text."""
        client = StubClient([
            tool_turn(text="Let me look that up.", call_id="c1"),
            answer("Two inlets."),
        ])
        loop = AgentLoop(client, registry(StubTool()))

        events = run(loop)

        text = [e.text for e in events if isinstance(e, TextDelta)]
        assert text == ["Two inlets."]
        assert loop.state.answer == "Two inlets."
        # The narration still went back to the model, in its own turn.
        assert client.requests[1].messages[-2]["content"][0]["text"] == "Let me look that up."

    def test_sources_are_emitted_before_text_with_no_chunks(self):
        """A failed search still yields the citations frame before the answer."""
        tool = StubTool(outcome=ToolOutcome(
            content="No documentation sections matched.",
            summary="no documentation matched 'xyz'",
            ok=True,
            data={"matched": 0},
        ))
        client = StubClient([tool_turn(args={"query": "xyz"}), answer("Not covered.")])
        loop = AgentLoop(client, registry(tool))

        events = run(loop)

        assert kinds(events) == [
            "ModelTurnEvent", "ToolCall", "ToolResult", "Sources", "TextDelta", "ModelTurnEvent"
        ]
        assert events[3].items == ()
        assert loop.state.retrieval_calls == 0

    def test_unknown_tool_is_reported_to_the_model(self):
        client = StubClient([tool_turn(name="run_patch"), answer("Sorry.")])
        loop = AgentLoop(client, registry(StubTool()))

        events = run(loop)

        result = next(e for e in events if isinstance(e, ToolResult))
        assert result.ok is False
        assert "no tool named 'run_patch'" in result.content
        assert "search_docs" in result.content
        assert loop.state.answer == "Sorry."
        assert loop.state.tool_calls == 1

    def test_tool_failure_does_not_raise(self):
        client = StubClient([tool_turn(), answer("Answer without docs.")])
        loop = AgentLoop(client, registry(StubTool(error=RuntimeError("voyage down"))))

        events = run(loop)

        result = next(e for e in events if isinstance(e, ToolResult))
        assert result.ok is False
        assert "voyage down" in result.content
        assert loop.state.answer == "Answer without docs."

    def test_multiple_tool_calls_in_one_turn(self):
        client = StubClient([
            {
                "text": "",
                "deltas": [],
                "tool_calls": [
                    {"id": "a", "name": "search_docs", "input": {"query": "osc~"}},
                    {"id": "b", "name": "search_docs", "input": {"query": "dac~"}},
                ],
            },
            answer("Both."),
        ])
        loop = AgentLoop(client, registry(StubTool()))

        events = run(loop)

        assert len([e for e in events if isinstance(e, ToolCall)]) == 2
        assert len(client.requests[1].messages[-1]["content"]) == 2
        assert loop.state.tool_calls == 2


class TestBudget:
    def test_turn_limit_forces_a_final_answer_with_tools_disabled(self):
        client = StubClient([tool_turn(call_id="c1"), answer("Using what I have.")])
        loop = AgentLoop(client, registry(StubTool()), AgentConfig(max_turns=1))

        events = run(loop)

        assert len(client.requests) == 2
        forced = client.requests[1]
        assert forced.tool_choice == {"type": "none"}
        assert "tool-call limit" in forced.system
        assert "Using what I have." in [e.text for e in events if isinstance(e, TextDelta)]
        assert loop.state.answer == "Using what I have."

    def test_uncooperative_final_turn_still_terminates(self):
        # Even with tools disabled the model asks for one; the loop must stop.
        client = StubClient([tool_turn(call_id="c1"), tool_turn(call_id="c2")])
        loop = AgentLoop(client, registry(StubTool()), AgentConfig(max_turns=1))

        events = run(loop)

        assert len(client.requests) == 2
        assert events[-1].__class__.__name__ in {"Sources", "TextDelta", "ToolResult"}
        assert loop.state.turns == 2

    def test_no_forced_answer_when_disabled(self):
        client = StubClient([tool_turn(call_id="c1")])
        loop = AgentLoop(
            client,
            registry(StubTool()),
            AgentConfig(max_turns=1, force_final_answer=False),
        )

        run(loop)

        assert len(client.requests) == 1
        assert loop.state.answer == ""

    def test_empty_model_stream_does_not_hang(self):
        class EmptyClient:
            def __init__(self):
                self.requests = []

            async def stream_turn(self, request):
                self.requests.append(request)
                if False:  # pragma: no cover - makes this an async generator
                    yield None

        loop = AgentLoop(EmptyClient(), registry(StubTool()))
        events = run(loop)

        assert kinds(events) == ["Sources"]
        assert loop.state.stop_reason == "error"


class TestModelTurnContract:
    def test_api_kwargs_include_optional_tools_and_tool_choice(self):
        request = ModelRequest(
            model="claude-haiku-4-5",
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            tools=[{"name": "search_docs"}],
            tool_choice={"type": "auto"},
            max_tokens=123,
        )
        kwargs = request.api_kwargs()
        assert kwargs["max_tokens"] == 123
        assert kwargs["tools"] == [{"name": "search_docs"}]
        assert kwargs["tool_choice"] == {"type": "auto"}
        assert kwargs["model"] == "claude-haiku-4-5"

    def test_api_kwargs_omit_tools_when_there_are_none(self):
        request = ModelRequest(model="m", system="s", messages=[])
        kwargs = request.api_kwargs()
        assert "tools" not in kwargs
        assert "tool_choice" not in kwargs
