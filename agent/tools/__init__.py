"""Tool contract and registry for the agent loop.

A tool is a name, a JSON schema the model sees, and a ``run(arguments)`` that
returns text for the model plus structured data for the caller. Tools are
synchronous on purpose: retrieval does blocking I/O, and the loop runs each tool
in a worker thread so the event loop keeps streaming.

Later subtasks (patch execution, skills, MCP) add tools here without touching the
loop. A tool that resolves something at run time (the skills directory) can expose
a ``refresh()``; the loop calls it once per turn.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Protocol, runtime_checkable

from ..types import Chunk

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolSpec:
    """What the model is told about a tool."""

    name: str
    description: str
    input_schema: dict[str, Any]
    #: True when the result cannot change the answer (the tool only adds output),
    #: so text the model wrote alongside the call is still its answer.
    output_only: bool = False

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


@runtime_checkable
class RefreshableTool(Protocol):
    """A tool whose advertised spec depends on state that can change between turns."""

    def refresh(self) -> None:
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

    def refresh(self) -> None:
        """Let refreshable tools re-read what they resolve at run time.

        The loop calls this at the start of every turn, so a skill added to the
        watched directory is advertised from the next turn on. A failing tool must
        not fail the run: the previous view stays in place and the error is logged.
        """
        for tool in self._tools.values():
            if not isinstance(tool, RefreshableTool):
                continue
            try:
                tool.refresh()
            except Exception:
                logger.warning(
                    "failed to refresh tool %s", self._tool_name(tool), exc_info=True
                )

    @staticmethod
    def _tool_name(tool: Tool) -> str:
        try:
            return tool.spec.name
        except Exception:
            return type(tool).__name__

    def filtered(self, allowed: Iterable[str]) -> "ToolRegistry":
        """Return a new registry restricted to tool names in ``allowed``."""
        allow_set = set(allowed)
        return ToolRegistry(
            tool for name, tool in self._tools.items() if name in allow_set
        )

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __iter__(self):
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)
