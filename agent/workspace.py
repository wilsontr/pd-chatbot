"""The patches one request has written, shared by its patch tools."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pd_schema import PdPatch
from sandbox.runner import SandboxResult

from . import artifacts
from .audio import analyze
from .types import Chunk


@dataclass
class PatchRun:
    result: SandboxResult
    errors: list[str]
    inputs: list[dict[str, Any]]
    analysis: dict[str, Any] | None = None

    @property
    def clean(self) -> bool:
        return self.result.ok and not self.errors

    def analyzed(self) -> dict[str, Any]:
        if self.analysis is None:
            self.analysis = analyze(self.result.wav)
        return self.analysis


@dataclass
class PatchEntry:
    name: str
    patch: PdPatch
    revisions: int = 1
    run: PatchRun | None = None  # of the current revision


@dataclass
class PatchWorkspace:
    """Latest revision of each patch, in the order they were first written."""

    entries: dict[str, PatchEntry] = field(default_factory=dict)
    runs: int = 0

    def write(self, name: str, patch: PdPatch) -> PatchEntry:
        entry = self.entries.get(name)
        if entry is None:
            entry = self.entries[name] = PatchEntry(name, patch)
        else:
            entry.patch = patch
            entry.revisions += 1
            entry.run = None
        return entry

    def patches(self) -> list[dict[str, Any]]:
        return [{"name": e.name, "patch": e.patch.model_dump()} for e in self.entries.values()]

    def audio(self) -> list[dict[str, Any]]:
        """Store the audio of each patch's current revision; silence is left out."""
        out = []
        for entry in self.entries.values():
            if entry.run is None or entry.run.analyzed()["silent"]:
                continue
            artifact_id = artifacts.put(entry.run.result.wav, "audio/wav")
            out.append({
                "id": artifact_id,
                "url": f"/artifacts/{artifact_id}",
                "name": entry.name,
                "mime": "audio/wav",
                "seconds": round(entry.run.result.seconds_rendered, 3),
            })
        return out

    def evidence(self) -> list[Chunk]:
        """What the runs showed, for checks that compare claims with sources."""
        out: list[Chunk] = []
        for entry in self.entries.values():
            if entry.run is None:
                continue
            run = entry.run
            out.append({
                "id": f"run:{entry.name}",
                "heading_path": f"Result of running the patch {entry.name!r} in Pd",
                "text": json.dumps({
                    "status": run.result.status,
                    "errors": run.errors,
                    "console": run.result.console[:1000],
                    "inputs": run.inputs,
                    "audio": run.analyzed(),
                }),
                "url": "",
                "source": "patch_run",
                "content_type": "patch_run",
                "object_name": None,
            })
        return out
