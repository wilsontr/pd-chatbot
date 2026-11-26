"""Which skill versions may serve, and the snapshots that keep them live.

A skill is a deployable unit. Its content is served only after the version pinned
in ``skills/activation.json`` has been activated — in CI, by passing its golden
slice (see ``evaluation.skills``). The manifest holds one entry per skill::

    {
      "version": 1,
      "skills": {
        "granular-synthesis": {
          "version": "41e503ae5451",
          "digest": "41e503ae5451…",
          "activated_at": "2026-11-26T00:00:00Z",
          "activated_by": "github-actions[bot]",
          "metrics": {"faithfulness": 4.8, "relevance": 4.9}
        }
      }
    }

The pinned identity is the content ``digest``, not the display ``version``: a
skill that declares ``version: 1.2.0`` is still served by content, so reusing a
version string cannot smuggle an ungated edit through. A snapshot of each
activated digest lives in ``skills/.activated/<name>/<digest>/``. That snapshot is what keeps the previous
version live: editing a skill folder changes its content digest, so a mismatch
between the working folder and the manifest serves the snapshot instead. Rolling
back is a manifest write, not a git revert.

The manifest and snapshots are written by the evaluation CLI; the runtime only
reads them. A skills directory **without** an activation manifest keeps the
development behaviour — every folder is served — so local experiments and the
test suite do not need to run the gate. ``SKILLS_ACTIVATION=off`` disables the
gate in a checked-out repository that does have a manifest.

This module deliberately depends on nothing else in the package: the discovery
code imports the file-walking helpers from here, and the evaluation code
imports the manifest writers, so neither direction can cycle.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

logger = logging.getLogger(__name__)

ACTIVATION_FILENAME = "activation.json"
ACTIVATED_DIR = ".activated"
MANIFEST_FORMAT_VERSION = 1

_IGNORED_DIRS = frozenset({"__pycache__", "node_modules"})
_IGNORED_FILES = frozenset({".DS_Store"})


class ActivationError(ValueError):
    """The activation manifest or a snapshot cannot be read."""


@dataclass(frozen=True)
class ActivationEntry:
    """One skill's pinned content identity and the evidence that activated it."""

    version: str
    digest: str = ""
    activated_at: str = ""
    activated_by: str = ""
    metrics: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "digest": self.digest,
            "activated_at": self.activated_at,
            "activated_by": self.activated_by,
            "metrics": dict(self.metrics),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ActivationEntry":
        digest = raw.get("digest")
        if not isinstance(digest, str) or not digest.strip():
            raise ActivationError("an activation entry has no 'digest'")
        version = str(raw.get("version") or "").strip() or digest.strip()[:12]
        metrics = raw.get("metrics") or {}
        if not isinstance(metrics, Mapping):
            raise ActivationError("an activation entry's 'metrics' must be an object")
        return cls(
            version=version,
            digest=digest.strip(),
            activated_at=str(raw.get("activated_at") or ""),
            activated_by=str(raw.get("activated_by") or ""),
            metrics=dict(metrics),
        )


def manifest_path(root: str | Path) -> Path:
    return Path(root).expanduser() / ACTIVATION_FILENAME


def activated_root(root: str | Path) -> Path:
    return Path(root).expanduser() / ACTIVATED_DIR


def snapshot_path(root: str | Path, name: str, digest: str) -> Path:
    """Where one activated digest is snapshotted (digest, never the display version)."""
    if not digest or any(char not in "0123456789abcdef" for char in digest):
        raise ActivationError(f"invalid content digest {digest!r} for {name!r}")
    return activated_root(root) / name / digest


def activation_enabled_by_env() -> bool:
    """False when ``SKILLS_ACTIVATION`` asks for the permissive view."""
    value = os.environ.get("SKILLS_ACTIVATION", "").strip().lower()
    return value not in {"off", "0", "false", "no", "permissive"}


def load_manifest(root: str | Path) -> dict[str, ActivationEntry] | None:
    """Read the manifest, or ``None`` when this directory has none.

    A malformed manifest raises: silently serving unvetted content would defeat
    the gate, and serving nothing on a typo is the safer failure. The registry
    turns the error into a diagnostic.
    """
    path = manifest_path(root)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ActivationError(f"cannot read {path.name}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ActivationError(f"{path.name} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ActivationError(f"{path.name} must be a JSON object")
    skills = raw.get("skills")
    if not isinstance(skills, dict):
        raise ActivationError(f"{path.name} has no 'skills' object")
    entries: dict[str, ActivationEntry] = {}
    for name, value in skills.items():
        if not isinstance(name, str) or not name.strip():
            raise ActivationError(f"{path.name} has an invalid skill name")
        if not isinstance(value, dict):
            raise ActivationError(f"{path.name}: entry for {name!r} is not an object")
        entries[name] = ActivationEntry.from_dict(value)
    return entries


def write_manifest(root: str | Path, entries: Mapping[str, ActivationEntry]) -> Path:
    """Write the manifest atomically, with skills in a stable order."""
    path = manifest_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": MANIFEST_FORMAT_VERSION,
        "skills": {name: entries[name].to_dict() for name in sorted(entries)},
    }
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


# --- Snapshot content helpers ------------------------------------------------


def iter_skill_files(directory: str | Path) -> list[Path]:
    """Every content file under a skill folder, relative and deterministically ordered."""
    base = Path(directory)
    files: list[Path] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(base)
        if any(part.startswith(".") or part in _IGNORED_DIRS for part in relative.parts):
            continue
        if relative.name in _IGNORED_FILES:
            continue
        files.append(relative)
    return files


def skill_digest(directory: str | Path, files: Iterable[Path] | None = None) -> str:
    """Hash a skill's files (paths and contents) into a stable content identity."""
    base = Path(directory)
    relative_files = list(iter_skill_files(base) if files is None else files)
    hasher = hashlib.sha256()
    for relative in relative_files:
        hasher.update(Path(relative).as_posix().encode("utf-8"))
        hasher.update(b"\0")
        try:
            with (base / relative).open("rb") as handle:
                for block in iter(lambda: handle.read(65536), b""):
                    hasher.update(block)
        except OSError:  # a file vanished mid-scan; the next refresh will catch it
            continue
        hasher.update(b"\0")
    return hasher.hexdigest()


def store_snapshot(
    root: str | Path,
    name: str,
    digest: str,
    source: str | Path,
) -> Path:
    """Copy one content digest of a skill into its snapshot directory.

    The copy is written to a temporary directory and renamed, so a crash cannot
    leave a half-snapshot that pretends to be an activated version. The snapshot
    is re-hashed and must equal ``digest``; a mismatch means the content changed
    while it was being activated, and it is not written.
    """
    source_path = Path(source)
    computed = skill_digest(source_path)
    if computed != digest:
        raise ActivationError(
            f"content of {name} does not match digest {digest[:12]} "
            f"(computed {computed[:12]})"
        )
    destination = snapshot_path(root, name, digest)
    if destination.is_dir() and skill_digest(destination) == digest:
        return destination  # already stored

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(destination.name + ".tmp")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    for relative in iter_skill_files(source_path):
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path / relative, target)
    if skill_digest(staging) != digest:
        shutil.rmtree(staging, ignore_errors=True)
        raise ActivationError(f"snapshot copy of {name} does not match its source")
    if destination.exists():
        shutil.rmtree(destination)
    staging.rename(destination)
    return destination


def prune_snapshots(root: str | Path, name: str, keep: int = 5) -> list[Path]:
    """Delete all but the newest ``keep`` snapshots for one skill."""
    skill_dir = activated_root(root) / name
    if not skill_dir.is_dir():
        return []
    snapshots = sorted(
        (path for path in skill_dir.iterdir() if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    removed: list[Path] = []
    for path in snapshots[keep:]:
        shutil.rmtree(path, ignore_errors=True)
        removed.append(path)
    return removed


__all__ = [
    "ACTIVATED_DIR",
    "ACTIVATION_FILENAME",
    "MANIFEST_FORMAT_VERSION",
    "ActivationEntry",
    "ActivationError",
    "activated_root",
    "activation_enabled_by_env",
    "iter_skill_files",
    "load_manifest",
    "manifest_path",
    "prune_snapshots",
    "skill_digest",
    "snapshot_path",
    "store_snapshot",
    "write_manifest",
]
