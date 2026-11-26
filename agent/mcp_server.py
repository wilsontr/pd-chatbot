"""The agent's tools as an MCP server (TASK-5.16).

Claude Code, Claude Desktop and any other MCP client get the same tools the web
agent uses — ``search_docs``, the patch tools, ``load_skill``, and ``run_patch``
/ ``analyze_audio`` when a sandbox backend is configured — with the client's own
model doing the reasoning. There is no model call here: the server is a thin
adapter from MCP ``tools/list`` and ``tools/call`` to the ``ToolRegistry``.

Guardrails are the ones every interface shares: the tool set comes from
``default_registry`` filtered by the ``mcp`` ``ToolPolicy``, and each call goes
through ``run_guarded_tool`` (policy check, exception capture, tool-output
injection screen). The patch tools' own limits (patches per session, sandbox
runs, run length, inputs) hold because one server process owns one
``PatchWorkspace`` — a stdio server is one client session.

Run it with ``python mcp_server.py`` from the repo root launcher, which fixes the
working directory so the corpus and Chroma index are found.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any, Collection, Mapping

import anyio
import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from pd_file import to_pd

from .guardrails import ToolPolicy, policy_for, run_guarded_tool
from .tools import ToolOutcome, ToolRegistry
from .workspace import PatchWorkspace

logger = logging.getLogger(__name__)

INTERFACE = "mcp"
SERVER_NAME = "pd-chatbot"
SERVER_VERSION = "0.1.0"

INSTRUCTIONS = (
    "Tools for Pure Data (Pd). Use search_docs before stating documented Pd "
    "behaviour and cite the 'Source:' URLs it returns; if it finds nothing, say "
    "the documentation does not cover it rather than answering from memory. "
    "write_patch validates a patch and returns it as a .pd file. When run_patch "
    "and analyze_audio are listed, use them to check that a patch loads without "
    "errors and makes the sound you expect before presenting it."
)

_READ_ONLY = types.ToolAnnotations(read_only_hint=True, open_world_hint=False)
_ANNOTATIONS: dict[str, types.ToolAnnotations] = {
    "search_docs": types.ToolAnnotations(
        title="Search the Pd documentation", read_only_hint=True, open_world_hint=False
    ),
    "list_patches": _READ_ONLY,
    "read_patch": _READ_ONLY,
    "load_skill": _READ_ONLY,
    "analyze_audio": _READ_ONLY,
    "write_patch": types.ToolAnnotations(
        title="Write a Pd patch",
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    ),
    "run_patch": types.ToolAnnotations(
        title="Run a patch in headless Pd",
        read_only_hint=False,
        destructive_hint=False,
        open_world_hint=False,
    ),
}


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


class PdToolServer:
    """Serves one ``ToolRegistry`` over MCP under the ``mcp`` interface policy."""

    def __init__(
        self,
        *,
        tools: ToolRegistry | None = None,
        workspace: PatchWorkspace | None = None,
        policy: ToolPolicy | None = None,
        approved_tools: Collection[str] = (),
    ):
        self.policy = policy or policy_for(INTERFACE)
        self.approved = frozenset(approved_tools)
        self.workspace = workspace if workspace is not None else PatchWorkspace()
        if tools is None:
            from .chat import default_registry

            tools = default_registry(
                self.workspace,
                interface=INTERFACE,
                policy=self.policy,
                approved_tools=self.approved,
            )
        self.tools = tools
        # The workspace is plain in-memory state; calls run one at a time so two
        # concurrent writes cannot interleave.
        self._lock = asyncio.Lock()

    def list_tools(self) -> list[types.Tool]:
        """The tools this client may call right now, with their own JSON schemas."""
        self.tools.refresh()
        permitted = self.policy.permitted_names(approved=self.approved)
        return [
            types.Tool(
                name=tool.spec.name,
                description=tool.spec.description,
                input_schema=tool.spec.input_schema,
                annotations=_ANNOTATIONS.get(tool.spec.name),
            )
            for tool in self.tools
            if tool.spec.name in permitted
        ]

    async def call_tool(
        self, name: str, arguments: Mapping[str, Any] | None
    ) -> types.CallToolResult:
        async with self._lock:
            self.tools.refresh()
            outcome = await asyncio.to_thread(
                run_guarded_tool,
                self.tools,
                name,
                dict(arguments or {}),
                policy=self.policy,
                approved=self.approved,
            )
        logger.info("mcp tool %s: %s", name, outcome.summary)
        return self._result(name, outcome)

    def _result(self, name: str, outcome: ToolOutcome) -> types.CallToolResult:
        content: list[types.ContentBlock] = [types.TextContent(text=outcome.content)]
        structured: dict[str, Any] = {
            "ok": outcome.ok,
            "summary": outcome.summary,
            "data": _json_safe(outcome.data),
        }
        if outcome.chunks:
            from .retrieval import chunks_to_sources

            structured["sources"] = _json_safe(chunks_to_sources(list(outcome.chunks)))
        if name == "write_patch" and outcome.ok:
            # An MCP client has no diagram renderer, so the patch reaches the
            # user as the .pd file it would otherwise download.
            entry = self.workspace.entries.get(str(outcome.data.get("name", "")))
            if entry is not None:
                pd_text = to_pd(entry.patch)
                structured["pd_file"] = pd_text
                content.append(
                    types.TextContent(
                        text=f"{entry.name}.pd (Pd file, save it to open in Pd):\n\n{pd_text}"
                    )
                )
        return types.CallToolResult(
            content=content,
            structured_content=structured,
            is_error=not outcome.ok,
        )

    def build(self) -> Server:
        async def on_list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
            return types.ListToolsResult(tools=self.list_tools())

        async def on_call_tool(
            ctx: Any, params: types.CallToolRequestParams
        ) -> types.CallToolResult:
            return await self.call_tool(params.name, params.arguments)

        return Server(
            SERVER_NAME,
            version=SERVER_VERSION,
            instructions=INSTRUCTIONS,
            on_list_tools=on_list_tools,
            on_call_tool=on_call_tool,
        )


async def serve_stdio(server: PdToolServer | None = None) -> None:
    mcp_server = (server or PdToolServer()).build()
    async with stdio_server() as (read_stream, write_stream):
        await mcp_server.run(
            read_stream, write_stream, mcp_server.create_initialization_options()
        )


def main() -> None:
    # stdout is the MCP channel; anything else must go to stderr.
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    anyio.run(serve_stdio)


if __name__ == "__main__":
    main()
