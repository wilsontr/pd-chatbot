"""The patches one request can edit, shared by its patch tools.

Within a request this holds the latest revision the agent wrote; across requests
``agent.sessions.SessionPatchStore`` seeds it from the conversation and saves
back what changed, so a follow-up edits the existing patch (TASK-5.12).
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any, Iterable

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
    #: Set when the patch is stored for a session (``agent.sessions``).
    patch_id: str | None = None
    #: Downloads the latest revision of ``patch_id`` as a .pd file.
    pd_url: str | None = None
    #: Written or repaired in the current run, so it belongs in this answer
    #: and must be persisted.
    touched: bool = False

    def to_artifact(self) -> dict[str, Any]:
        """The ``pd_patch`` artifact shape: the diagram plus how to get its .pd."""
        data: dict[str, Any] = {
            "name": self.name,
            "patch": self.patch.model_dump(),
            "revision": self.revisions,
        }
        if self.patch_id is not None:
            data["id"] = self.patch_id
        if self.pd_url is not None:
            data["pd_url"] = self.pd_url
        return data


@dataclass
class PatchWorkspace:
    """Latest revision of each patch, in the order they were first written.

    Within a request the agent edits what it wrote; across requests
    ``agent.sessions.SessionPatchStore`` seeds the workspace from the session
    and writes back the entries marked ``touched``.
    """

    entries: dict[str, PatchEntry] = field(default_factory=dict)
    runs: int = 0

    def write(self, name: str, patch: PdPatch) -> PatchEntry:
        entry = self.entries.get(name)
        if entry is None:
            entry = self.entries[name] = PatchEntry(name, patch, touched=True)
        else:
            entry.patch = patch
            entry.revisions += 1
            entry.run = None
            entry.touched = True
        return entry

    def seed(self, entries: Iterable[PatchEntry]) -> None:
        """Add stored patches without replacing anything already written."""
        for entry in entries:
            self.entries.setdefault(entry.name, entry)

    def find(self, patch_id_or_name: str) -> PatchEntry | None:
        """Look a patch up by name, or by the id a stored patch was given."""
        entry = self.entries.get(patch_id_or_name)
        if entry is not None:
            return entry
        for candidate in self.entries.values():
            if candidate.patch_id is not None and candidate.patch_id == patch_id_or_name:
                return candidate
        return None

    def patches(self, *, answered_only: bool = False) -> list[dict[str, Any]]:
        """Artifact data for every patch, or only the ones this answer is about.

        With ``answered_only`` a patch is included when this run wrote or
        repaired it, or ran it (a run's audio is part of the answer). Patches
        seeded from an earlier turn are left out unless they changed.
        """
        return [
            entry.to_artifact()
            for entry in self.entries.values()
            if not answered_only or entry.touched or entry.run is not None
        ]

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

    def to_snapshot(self) -> dict[str, Any]:
        """Serialize workspace state into a JSON-safe dict for durable step checkpoints."""
        serialized_entries: list[dict[str, Any]] = []
        for entry in self.entries.values():
            run_data = None
            if entry.run is not None:
                res = entry.run.result
                run_data = {
                    "result": {
                        "status": res.status,
                        "exit_code": res.exit_code,
                        "signal": res.signal,
                        "console": res.console,
                        "console_truncated": res.console_truncated,
                        "wav_b64": base64.b64encode(res.wav).decode("ascii"),
                        "seconds_rendered": res.seconds_rendered,
                        "peak": res.peak,
                        "nonfinite_samples": res.nonfinite_samples,
                    },
                    "errors": list(entry.run.errors),
                    "inputs": list(entry.run.inputs),
                    "analysis": entry.run.analysis,
                }
            serialized_entries.append({
                "name": entry.name,
                "patch": entry.patch.model_dump(),
                "revisions": entry.revisions,
                "run": run_data,
                "patch_id": entry.patch_id,
                "pd_url": entry.pd_url,
                "touched": entry.touched,
            })
        return {"runs": self.runs, "entries": serialized_entries}

    @classmethod
    def from_snapshot(cls, snapshot: dict[str, Any] | None) -> "PatchWorkspace":
        """Rehydrate a ``PatchWorkspace`` from a durable checkpoint snapshot."""
        ws = cls()
        if not snapshot:
            return ws
        ws.runs = int(snapshot.get("runs", 0))
        for item in snapshot.get("entries", []):
            patch = PdPatch.model_validate(item["patch"])
            run_obj: PatchRun | None = None
            raw_run = item.get("run")
            if raw_run is not None:
                r = raw_run["result"]
                sb = SandboxResult(
                    status=r["status"],
                    exit_code=int(r.get("exit_code", 0)),
                    signal=int(r.get("signal", 0)),
                    console=r["console"],
                    console_truncated=bool(r.get("console_truncated", False)),
                    wav=base64.b64decode(r.get("wav_b64", "")),
                    seconds_rendered=float(r.get("seconds_rendered", 0.0)),
                    peak=float(r.get("peak", 0.0)),
                    nonfinite_samples=int(r.get("nonfinite_samples", 0)),
                )
                run_obj = PatchRun(
                    result=sb,
                    errors=list(raw_run.get("errors", [])),
                    inputs=list(raw_run.get("inputs", [])),
                    analysis=raw_run.get("analysis"),
                )
            ws.entries[item["name"]] = PatchEntry(
                name=item["name"],
                patch=patch,
                revisions=int(item.get("revisions", 1)),
                run=run_obj,
                patch_id=item.get("patch_id"),
                pd_url=item.get("pd_url"),
                touched=bool(item.get("touched", False)),
            )
        return ws
