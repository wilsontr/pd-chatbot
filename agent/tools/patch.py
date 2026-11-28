"""Patch tools: ``list_patches``, ``read_patch``, ``write_patch``, ``run_patch``
and ``analyze_audio``.

``write_patch`` is how every patch reaches the user. ``list_patches`` and
``read_patch`` expose the patches stored for the conversation session so a
follow-up can edit one instead of starting over (TASK-5.12). The last two need
the sandbox (docs/SANDBOX.md) and are only offered when execution is enabled.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import replace
from typing import Any, Callable, Mapping

import newrelic.agent
from pydantic import ValidationError

from pd_file import escape_text, to_pd
from pd_schema import PD_OBJECT_TYPES, PdPatch
from sandbox import SandboxError, SandboxResult
from sandbox import run_patch as sandbox_run

from ..workspace import PatchEntry, PatchRun, PatchWorkspace
from . import Tool, ToolOutcome, ToolSpec

logger = logging.getLogger(__name__)

MAX_RUN_SECONDS = 10.0
DEFAULT_RUN_SECONDS = 2.0
MAX_RUNS = 6
#: A session keeps its patches across turns, so this is a ceiling on the whole
#: conversation workspace rather than on one answer.
MAX_PATCHES = 12
MAX_INPUTS = 8
_MAX_CONSOLE_CHARS = 3000
_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
_NAME_SCHEMA = {
    "type": "string",
    "description": "Short lower-case slug for the patch, such as 'fm-bell'.",
}
_ID_SCHEMA = {
    "type": "string",
    "description": "A patch's stored id, from `list_patches` or `read_patch`.",
}
_NO_INLET_TYPES = {"comment", "cnv", "table"}
_VERBOSE_RE = re.compile(r"^verbose\(\d+\): ")
_ERROR_RE = re.compile(
    r"^error\b|couldn't create|connection failed|cannot connect|no method for|no such object",
    re.IGNORECASE,
)


def execution_enabled() -> bool:
    """Whether patches may be run: a sandbox backend must be chosen explicitly."""
    return bool(os.environ.get("PD_SANDBOX_BACKEND"))


def clean_console(console: str) -> str:
    return "\n".join(_VERBOSE_RE.sub("", line) for line in console.splitlines())


def console_errors(console: str) -> list[str]:
    """Pd's complaints; a ``[print]`` box's own output is never one."""
    lines = clean_console(console).splitlines()
    return [
        line for line in lines
        if _ERROR_RE.search(line) and not re.match(r"print\S*:|\S+: error", line)
    ]


def runnable_file(patch: PdPatch, inputs: list[dict[str, Any]]) -> str:
    """The patch plus boxes that send each input to its target while it runs."""
    ids = {obj.id: i for i, obj in enumerate(patch.objects)}
    lines = [to_pd(patch)]
    wires = []
    base = len(patch.objects)
    for n, item in enumerate(inputs):
        first = base + 3 * n
        lines += [
            "#X obj 0 0 loadbang;",
            f"#X obj 0 0 delay {item['at_ms']:g};",
            f"#X msg 0 0 {escape_text(item['value'])};",
        ]
        wires += [
            f"#X connect {first} 0 {first + 1} 0;",
            f"#X connect {first + 1} 0 {first + 2} 0;",
            f"#X connect {first + 2} 0 {ids[item['id']]} 0;",
        ]
    return "\n".join([*lines, *wires]) + "\n"


def parse_inputs(raw: Any, patch: PdPatch, seconds: float) -> list[dict[str, Any]]:
    """Validate simulated inputs; raises ValueError with a message for the model."""
    if raw in (None, []):
        return []
    if not isinstance(raw, list) or len(raw) > MAX_INPUTS:
        raise ValueError(f"inputs must be a list of at most {MAX_INPUTS} items")
    boxes = {obj.id: obj for obj in patch.objects}
    out = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError("each input needs id, value and at_ms")
        box = boxes.get(str(item.get("id")))
        if box is None or box.type in _NO_INLET_TYPES:
            raise ValueError(f"input id {item.get('id')!r} is not a box with an inlet")
        value = str(item.get("value", "")).strip()
        if not value or len(value) > 100 or any(c in value for c in ",;\\$\n"):
            raise ValueError("an input value is one message, such as 'bang', '0.5' or '440 100'")
        try:
            at_ms = float(item.get("at_ms", 0))
        except (TypeError, ValueError):
            raise ValueError("at_ms must be a number") from None
        if not 0 <= at_ms <= seconds * 1000:
            raise ValueError("at_ms must fall within the run")
        out.append({"id": box.id, "value": value, "at_ms": at_ms})
    return out


class _PatchTool:
    def __init__(self, workspace: PatchWorkspace):
        self.workspace = workspace

    def _entry(self, arguments: Mapping[str, Any]) -> PatchEntry | ToolOutcome:
        key = str(arguments.get("id") or arguments.get("name") or "")
        if not key:
            return _failure(
                "Pass the patch's id (from `list_patches`) or the name it was written under."
            )
        entry = self.workspace.find(key)
        if entry is not None:
            return entry
        known = ", ".join(
            f"{e.patch_id} ({e.name})" if e.patch_id else e.name
            for e in self.workspace.entries.values()
        ) or "none yet"
        return _failure(
            f"There is no patch with id or name {key!r}. Patches in this session: {known}."
        )

    def _named(self, arguments: Mapping[str, Any], outcome: ToolOutcome) -> ToolOutcome:
        """Give a failure the stored name of the patch it was about.

        A call may name a patch by its id or its name; the name in the result is
        what lets a later clean call of the same patch count as recovering from
        this failure, however each call referred to it (TASK-5.32).
        """
        if outcome.ok or outcome.data.get("name"):
            return outcome
        key = str(arguments.get("id") or arguments.get("name") or "")
        entry = self.workspace.find(key) if key else None
        if entry is None:
            return outcome
        return replace(outcome, data={**outcome.data, "name": entry.name})


def _failure(message: str, **data: Any) -> ToolOutcome:
    return ToolOutcome(content=message, summary=message[:120], ok=False, data=data)


class ListPatchesTool(_PatchTool):
    spec = ToolSpec(
        name="list_patches",
        description=(
            "List the patches stored for this conversation, with each patch's id, name and "
            "revision. Use it when the user refers to a patch from an earlier turn, before "
            "reading or changing it."
        ),
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    )

    def run(self, arguments: Mapping[str, Any]) -> ToolOutcome:
        items = [
            {
                "id": entry.patch_id,
                "name": entry.name,
                "revision": entry.revisions,
                "boxes": len(entry.patch.objects),
            }
            for entry in self.workspace.entries.values()
        ]
        if not items:
            return ToolOutcome(
                content="No patches have been written in this conversation yet.",
                summary="no stored patches",
                data={"patches": []},
            )
        return ToolOutcome(
            content=json.dumps({"patches": items}),
            summary=f"{len(items)} patch{'es' if len(items) != 1 else ''} in this session",
            data={"patches": items},
        )


class ReadPatchTool(_PatchTool):
    spec = ToolSpec(
        name="read_patch",
        description=(
            "Load a patch stored in this conversation — by id, or by the name it was "
            "written under — with its full objects and connections. Your earlier patch "
            "JSON is not in the conversation history, so read a patch before editing it."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "id": _ID_SCHEMA,
                "name": _NAME_SCHEMA,
            },
            "additionalProperties": False,
        },
    )

    def run(self, arguments: Mapping[str, Any]) -> ToolOutcome:
        entry = self._entry(arguments)
        if isinstance(entry, ToolOutcome):
            return entry
        content = json.dumps({
            "id": entry.patch_id,
            "name": entry.name,
            "revision": entry.revisions,
            "patch": entry.patch.model_dump(),
        })
        return ToolOutcome(
            content=content,
            summary=f"read {entry.name} (rev {entry.revisions}, {len(entry.patch.objects)} boxes)",
            data={
                "name": entry.name,
                "id": entry.patch_id,
                "revision": entry.revisions,
                "boxes": len(entry.patch.objects),
            },
        )


class WritePatchTool(_PatchTool):
    spec = ToolSpec(
        name="write_patch",
        output_only=True,
        description=(
            "Write a Pd patch. This is the only way to give the user a patch: it is drawn "
            "as a diagram under your answer and can be downloaded as a .pd file. Pass the "
            "`id` of a stored patch (from `list_patches`) to replace it in place, or reuse "
            "its name; a new name starts a new patch. Keep patches to 15 boxes; for a "
            "larger one, write the key signal path."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "id": _ID_SCHEMA,
                "name": _NAME_SCHEMA,
                "objects": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 15,
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "type": {"type": "string", "enum": list(PD_OBJECT_TYPES)},
                            "text": {
                                "type": "string",
                                "description": (
                                    "What the box would be created with in Pd, e.g. 'osc~ 440'. "
                                    "For a widget, just its type name."
                                ),
                            },
                            "inlets": {"type": "integer", "minimum": 0},
                            "outlets": {"type": "integer", "minimum": 0},
                        },
                        "required": ["id", "type", "text", "inlets", "outlets"],
                        "additionalProperties": False,
                    },
                },
                "connections": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "srcId": {"type": "string"},
                            "srcOutlet": {"type": "integer", "minimum": 0},
                            "dstId": {"type": "string"},
                            "dstInlet": {"type": "integer", "minimum": 0},
                        },
                        "required": ["srcId", "srcOutlet", "dstId", "dstInlet"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["objects", "connections"],
            "additionalProperties": False,
        },
    )

    def run(self, arguments: Mapping[str, Any]) -> ToolOutcome:
        return self._named(arguments, self._run(arguments))

    def _run(self, arguments: Mapping[str, Any]) -> ToolOutcome:
        patch_id = str(arguments.get("id") or "")
        name = str(arguments.get("name") or "")
        existing: PatchEntry | None = None
        if patch_id:
            existing = self.workspace.find(patch_id)
            if existing is None:
                known = ", ".join(
                    f"{e.patch_id} ({e.name})"
                    for e in self.workspace.entries.values() if e.patch_id
                ) or "none yet"
                return _failure(
                    f"There is no patch with id {patch_id!r} in this session. "
                    f"Stored patch ids: {known}."
                )
            # The stored name is the patch's identity; the id only says which one.
            name = existing.name
        else:
            if not _NAME_RE.fullmatch(name):
                return _failure(
                    "name must be a lower-case slug such as 'fm-bell', or pass the id of an "
                    "existing patch."
                )
            existing = self.workspace.entries.get(name)
        try:
            patch = PdPatch.model_validate({
                "objects": arguments.get("objects"),
                "connections": arguments.get("connections") or [],
            })
        except ValidationError as exc:
            newrelic.agent.record_custom_metric("Custom/PdPatch/ParseFailure", 1)
            problems = "; ".join(
                f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:5]
            )
            return _failure(f"The patch was not saved: {problems}.", invalid_patch=True)
        ids = [obj.id for obj in patch.objects]
        if len(set(ids)) != len(ids):
            return _failure("The patch was not saved: object ids must be unique.", invalid_patch=True)
        missing = sorted(
            {i for c in patch.connections for i in (c.srcId, c.dstId)} - set(ids)
        )
        if missing:
            return _failure(
                f"The patch was not saved: connections use unknown ids {', '.join(missing)}.",
                invalid_patch=True,
            )
        if existing is None and len(self.workspace.entries) >= MAX_PATCHES:
            return _failure(
                f"At most {MAX_PATCHES} patches per conversation; replace one of the "
                "existing patches instead."
            )
        entry = self.workspace.write(name, patch)
        data: dict[str, Any] = {"name": name, "revision": entry.revisions}
        if entry.patch_id is not None:
            data["id"] = entry.patch_id
        return ToolOutcome(
            content=f"Saved patch {name!r} (revision {entry.revisions}).",
            summary=f"wrote {name} ({len(ids)} boxes)",
            data=data,
        )


class RunPatchTool(_PatchTool):
    spec = ToolSpec(
        name="run_patch",
        description=(
            "Run a patch you wrote in headless Pd with audio on, and get back its status and "
            "console output, including Pd errors such as \"couldn't create\". Nobody clicks or "
            "types during a run, so a patch that waits for a bang, message box, slider or "
            "number box stays silent unless you simulate that with `inputs`."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "id": _ID_SCHEMA,
                "name": _NAME_SCHEMA,
                "seconds": {
                    "type": "number",
                    "description": f"Audio to render, 0.1 to {MAX_RUN_SECONDS:g} (default 2).",
                },
                "inputs": {
                    "type": "array",
                    "maxItems": MAX_INPUTS,
                    "description": "Messages to send to a box's left inlet during the run.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "The box's id"},
                            "value": {
                                "type": "string",
                                "description": "'bang', a number, or a list such as '1 100'",
                            },
                            "at_ms": {"type": "number", "description": "When (default 0)"},
                        },
                        "required": ["id", "value"],
                        "additionalProperties": False,
                    },
                },
            },
            "additionalProperties": False,
        },
    )

    def __init__(
        self,
        workspace: PatchWorkspace,
        runner: Callable[..., SandboxResult] = sandbox_run,
    ):
        super().__init__(workspace)
        self._runner = runner

    def run(self, arguments: Mapping[str, Any]) -> ToolOutcome:
        return self._named(arguments, self._run(arguments))

    def _run(self, arguments: Mapping[str, Any]) -> ToolOutcome:
        entry = self._entry(arguments)
        if isinstance(entry, ToolOutcome):
            return entry
        try:
            seconds = float(arguments.get("seconds") or DEFAULT_RUN_SECONDS)
        except (TypeError, ValueError):
            return _failure("seconds must be a number.")
        if not 0.1 <= seconds <= MAX_RUN_SECONDS:
            return _failure(f"seconds must be from 0.1 to {MAX_RUN_SECONDS:g}.")
        try:
            inputs = parse_inputs(arguments.get("inputs"), entry.patch, seconds)
        except ValueError as exc:
            return _failure(f"The patch was not run: {exc}.")
        if self.workspace.runs >= MAX_RUNS:
            return _failure(
                f"The limit of {MAX_RUNS} runs is used up. Answer with what you have and say "
                "what you could not verify."
            )
        self.workspace.runs += 1
        try:
            result = self._runner(runnable_file(entry.patch, inputs), seconds)
        except SandboxError:
            # Infrastructure detail stays in the logs; it is no use to the model.
            logger.warning("sandbox run failed", exc_info=True)
            newrelic.agent.record_custom_metric("Custom/Sandbox/Error", 1)
            return _failure(
                "The sandbox is unavailable, so the patch was not run. Do not retry. Answer "
                "without it, and say the patch has not been run."
            )
        newrelic.agent.record_custom_metric(f"Custom/Sandbox/Run/{result.status}", 1)
        entry.run = PatchRun(result, console_errors(result.console), inputs)
        console = clean_console(result.console)
        report = {
            "status": result.status,
            "ran_cleanly": entry.run.clean,
            "seconds_rendered": round(result.seconds_rendered, 3),
            "errors": entry.run.errors[:20],
            "console": console[:_MAX_CONSOLE_CHARS],
            "console_truncated": result.console_truncated or len(console) > _MAX_CONSOLE_CHARS,
            "silent": entry.run.analyzed()["silent"],
        }
        return ToolOutcome(
            content=json.dumps(report),
            summary=(
                f"ran {entry.name}: "
                + ("clean" if entry.run.clean
                   else f"{len(entry.run.errors)} error(s)" if result.ok else result.status)
            ),
            data={
                "name": entry.name, "status": result.status,
                "ran_cleanly": entry.run.clean, "errors": entry.run.errors[:5],
            },
        )


class AnalyzeAudioTool(_PatchTool):
    spec = ToolSpec(
        name="analyze_audio",
        description=(
            "Measure the audio from the latest run of a patch: level, peak, clipping, silence, "
            "DC offset, dominant frequency, strongest spectral peaks, and how level and pitch "
            "change over eight equal segments."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "id": _ID_SCHEMA,
                "name": _NAME_SCHEMA,
            },
            "additionalProperties": False,
        },
    )

    def run(self, arguments: Mapping[str, Any]) -> ToolOutcome:
        return self._named(arguments, self._run(arguments))

    def _run(self, arguments: Mapping[str, Any]) -> ToolOutcome:
        entry = self._entry(arguments)
        if isinstance(entry, ToolOutcome):
            return entry
        if entry.run is None:
            return _failure(f"Run {entry.name!r} first: its current revision has not been run.")
        analysis = {
            **entry.run.analyzed(),
            # From before 16-bit clipping, so it can show by how much it clips.
            "peak_before_clipping": round(entry.run.result.peak, 4),
            "nonfinite_samples": entry.run.result.nonfinite_samples,
        }
        hz = analysis.get("dominant_hz")
        summary = "silent" if analysis["silent"] else f"peak {analysis['peak']}, {hz} Hz"
        return ToolOutcome(
            content=json.dumps(analysis),
            summary=f"analyzed {entry.name}: {summary}",
            data={"name": entry.name, "silent": analysis["silent"], "dominant_hz": hz,
                  "peak": analysis.get("peak")},
        )


def patch_tools(workspace: PatchWorkspace, *, execution: bool) -> list[Tool]:
    tools: list[Tool] = [
        ListPatchesTool(workspace),
        ReadPatchTool(workspace),
        WritePatchTool(workspace),
    ]
    if execution:
        tools += [RunPatchTool(workspace), AnalyzeAudioTool(workspace)]
    return tools
