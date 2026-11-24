"""Tool contract and registry for the agent loop.

A tool is a name, a JSON schema the model sees, and a ``run(arguments)`` that
returns text for the model plus structured data for the caller. Tools are
synchronous on purpose: retrieval does blocking I/O, and the loop runs each tool
in a worker thread so the event loop keeps streaming.

Later subtasks (patch execution, skills, MCP) add tools here without touching the
loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Protocol, runtime_checkable

from ..types import Chunk


@dataclass(frozen=True)
class ToolSpec:
    """What the model is told about a tool."""

    name: str
    description: str
    input_schema: dict[str, Any]

    def to_api(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


@dataclass(frozen=True)
class ToolOutcome:
    """What a tool produced.

    ``content`` is the text the model reads; ``summary`` is the one-line
    description shown next to the step in a UI; ``chunks`` are retrieved parent
    sections, which become the answer's citations and the faithfulness guard's
    evidence.
    """

    content: str
    summary: str
    ok: bool = True
    chunks: tuple[Chunk, ...] = ()
    data: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Tool(Protocol):
    spec: ToolSpec

    def run(self, arguments: Mapping[str, Any]) -> ToolOutcome:
        ...


class ToolRegistry:
    """The tools one run may use, in the order the model sees them."""

    def __init__(self, tools: Iterable[Tool] = ()):
        self._tools: dict[str, Tool] = {tool.spec.name: tool for tool in tools}

    def add(self, tool: Tool) -> "ToolRegistry":
        self._tools[tool.spec.name] = tool
        return self

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def specs(self) -> list[dict[str, Any]]:
        return [tool.spec.to_api() for tool in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __iter__(self):
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)
