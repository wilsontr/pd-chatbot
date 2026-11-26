"""A runtime registry of Agent Skills read from a watched directory.

A skill is a folder with a ``SKILL.md`` and any files its instructions refer to::

    skills/granular-synthesis/
        SKILL.md          # front matter + instructions
        scripts/grain.py
        examples/grain-cloud.pd

``SKILL.md`` opens with flat front matter delimited by ``---``::

    ---
    name: granular-synthesis
    description: Grain clouds in Pd — windowing, overlap, and pitch scatter.
    version: 3          # optional
    ---

    ...the instructions the model reads when it loads the skill...

Only ``name`` and ``description`` are shown to the model up front; the body is
returned by the ``load_skill`` tool when the model asks for it. That progressive
disclosure is the point: the catalogue stays small while a project can carry many
skills with long instructions.

The registry is a *view of the directory*, not a copy of it. ``refresh()``
re-reads the whole folder and ``get()`` re-reads one skill from disk, so adding
or editing a skill takes effect without restarting the process. Every skill has a
version: the declared ``version`` when the front matter has one, otherwise a
digest of the skill's files, so an edit is visible even when nobody bumped a
number.

Folder layout and failure handling: a directory without a ``SKILL.md`` is not a
skill (the ``skills/`` folder's own README lives there), and a malformed skill is
skipped with a diagnostic instead of breaking the registry — one bad file must
not take the agent's other skills offline.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Collection

from .activation import (
    ACTIVATION_FILENAME,
    ActivationEntry,
    ActivationError,
    activation_enabled_by_env,
    iter_skill_files,
    load_manifest,
    manifest_path,
    skill_digest,
    snapshot_path,
)

logger = logging.getLogger(__name__)

SKILL_FILENAME = "SKILL.md"
#: The skill's eval slice. It is part of the folder (and of the version digest,
#: so changing the slice bumps the version) but it is not a resource for the
#: model, so it is left out of the bundled-file list `load_skill` returns.
GOLDEN_FILENAME = "golden.json"
_ACCEPTED_FILENAMES = (SKILL_FILENAME, "skill.md")

#: Names appear in tool arguments and in the record of what a run loaded, so
#: keep them URL-and-log friendly.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

#: A skill description is a one-line trigger, not a document.
MAX_DESCRIPTION_CHARS = 500

DEFAULT_SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"


class SkillError(ValueError):
    """A folder that looks like a skill but cannot be served as one."""

    def __init__(self, path: Path, message: str):
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


@dataclass(frozen=True)
class Skill:
    """One loadable skill and the version the run will record."""

    name: str
    description: str
    version: str
    digest: str
    path: Path
    instructions: str
    #: Files bundled with the skill, relative to ``path`` and excluding SKILL.md.
    files: tuple[str, ...] = ()
    declared_version: str | None = None

    def to_record(self) -> dict[str, str]:
        """The compact record a run stores of the skill version it loaded."""
        return {"name": self.name, "version": self.version, "digest": self.digest}


@dataclass(frozen=True)
class SkillDiagnostic:
    """A folder that was skipped, and why."""

    path: Path
    message: str


def default_skills_root() -> Path:
    """The watched directory: ``SKILLS_DIR`` if set, else ``<repo>/skills``."""
    configured = os.environ.get("SKILLS_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_SKILLS_DIR


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """Split a SKILL.md into its front-matter fields and its instructions body.

    Supports the flat ``key: value`` subset the Agent Skills format uses: one
    field per line, an optional matching pair of quotes around the value, and
    ``#`` comments. Nested YAML is not needed and is not parsed. A document with
    no (or unterminated) front matter is all body.
    """
    if text.startswith("\ufeff"):
        text = text[1:]
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    fields: dict[str, str] = {}
    for index in range(1, len(lines)):
        line = lines[index]
        if line.strip() == "---":
            return fields, "\n".join(lines[index + 1:]).strip("\n")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line[:1].isspace():
            # Indented continuation of a nested structure we do not support.
            continue
        key, separator, value = line.partition(":")
        if not separator:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        fields[key.strip().lower()] = value
    # No closing delimiter: this is not front matter after all.
    return {}, text


def _read_skill(directory: Path, *, expected_name: str | None = None) -> Skill | None:
    """Read one folder into a ``Skill``, or ``None`` when it is not a skill folder.

    ``expected_name`` is the name the skill must claim. It defaults to the
    folder's own name; an activated snapshot passes the skill name because its
    folder is named after the version.

    Raises ``SkillError`` for a folder that has a ``SKILL.md`` but cannot be
    served (bad name, missing description, empty instructions).
    """
    expected = expected_name or directory.name
    skill_file = next(
        (directory / name for name in _ACCEPTED_FILENAMES if (directory / name).is_file()),
        None,
    )
    if skill_file is None:
        return None

    try:
        raw = skill_file.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise SkillError(directory, f"cannot read {skill_file.name}: {exc}") from exc

    fields, body = parse_front_matter(raw)
    name = (fields.get("name") or directory.name).strip()
    description = " ".join((fields.get("description") or "").split())
    declared_version = (fields.get("version") or "").strip() or None

    if not _NAME_RE.match(name):
        raise SkillError(
            directory,
            f"invalid skill name {name!r}; use lowercase letters, digits, '-', '_' or '.'",
        )
    if name != expected:
        raise SkillError(
            directory,
            f"front-matter name {name!r} does not match {expected!r}; a skill's "
            "name must be stable so it can be found",
        )
    if not description:
        raise SkillError(directory, "SKILL.md has no 'description' front-matter field")
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise SkillError(
            directory,
            f"description is {len(description)} characters; keep it to a one-line "
            f"trigger of at most {MAX_DESCRIPTION_CHARS}",
        )

    instructions = body.strip()
    if not instructions:
        raise SkillError(directory, "SKILL.md has front matter but no instructions")

    files = iter_skill_files(directory)
    digest = skill_digest(directory, files)
    bundled = tuple(
        relative.as_posix()
        for relative in files
        if relative.as_posix() not in (*_ACCEPTED_FILENAMES, GOLDEN_FILENAME)
    )
    return Skill(
        name=name,
        description=description,
        version=declared_version or digest[:12],
        digest=digest,
        path=directory,
        instructions=instructions,
        files=bundled,
        declared_version=declared_version,
    )


class SkillRegistry:
    """The skills currently installed in a watched directory.

    ``refresh()`` replaces the catalogue with a fresh scan; ``get()`` reads one
    skill from disk so an edited ``SKILL.md`` is served without a restart.
    Instances are cheap and are not shared between requests, which keeps a
    mid-request refresh from leaking between concurrent runs.

    When the directory has an activation manifest, only the pinned version of a
    skill is served; an edited working folder is ignored until its version is
    activated, and the activated snapshot keeps serving in the meantime. A
    directory without a manifest is the development view: every folder is served.
    Pass ``activation=False`` to force that view, or set ``SKILLS_ACTIVATION=off``.
    """

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        activation: bool | None = None,
        only: Collection[str] | None = None,
    ):
        self.root: Path = Path(root).expanduser() if root is not None else default_skills_root()
        self._activation = activation
        self._only = frozenset(only) if only is not None else None
        self._skills: dict[str, Skill] = {}
        self._diagnostics: list[SkillDiagnostic] = []
        self._manifest: dict[str, ActivationEntry] = {}
        self._strict = False
        self._loaded = False

    # -- activation ----------------------------------------------------------

    @property
    def strict(self) -> bool:
        """True when only activated versions are served."""
        if not self._loaded:
            self.refresh()
        return self._strict

    def _use_activation(self) -> bool:
        if self._activation is False:
            return False
        if self._activation is True:
            return True
        return activation_enabled_by_env() and manifest_path(self.root).is_file()

    def _apply_activation(self, skill: Skill) -> Skill | None:
        """Return the version this skill may serve, or ``None`` when none may."""
        if not self._strict:
            return skill
        entry = self._manifest.get(skill.name)
        if entry is None:
            self._diagnostics.append(SkillDiagnostic(
                skill.path,
                f"not activated: no entry for {skill.name!r} in activation.json",
            ))
            logger.warning("skill %r is not activated; not serving it", skill.name)
            return None
        if entry.digest == skill.digest:
            return skill

        try:
            snapshot = snapshot_path(self.root, skill.name, entry.digest)
        except ActivationError as exc:  # corrupt manifest entry
            self._diagnostics.append(SkillDiagnostic(manifest_path(self.root), str(exc)))
            return None
        if not snapshot.is_dir():
            self._diagnostics.append(SkillDiagnostic(
                skill.path,
                f"working content {skill.version} is not activated and the "
                f"activated version {entry.version} has no snapshot",
            ))
            logger.warning(
                "skill %r is at %s but %s is activated and has no snapshot",
                skill.name, skill.version, entry.version,
            )
            return None
        try:
            activated = _read_skill(snapshot, expected_name=skill.name)
        except SkillError as exc:
            self._diagnostics.append(SkillDiagnostic(
                snapshot, f"activated snapshot is unreadable: {exc.message}"
            ))
            logger.warning("activated snapshot for %r is unreadable: %s", skill.name, exc.message)
            return None
        if activated is None or activated.digest != entry.digest:
            self._diagnostics.append(SkillDiagnostic(
                snapshot,
                f"activated snapshot does not match pinned digest {entry.digest[:12]}",
            ))
            logger.warning(
                "activated snapshot for %r does not match %s",
                skill.name, entry.digest[:12],
            )
            return None
        logger.debug(
            "serving activated version %s (%s) of %r; working folder is %s",
            entry.version, entry.digest[:12], skill.name, skill.version,
        )
        return activated

    # -- discovery -----------------------------------------------------------

    def refresh(self) -> tuple[Skill, ...]:
        """Re-read the watched directory and return the catalogue, sorted by name."""
        self._skills = {}
        self._diagnostics = []
        self._loaded = True
        self._strict = self._use_activation()
        self._manifest = {}
        if self._strict:
            try:
                self._manifest = load_manifest(self.root) or {}
            except ActivationError as exc:
                # Fail closed: an unreadable manifest cannot vouch for anything.
                self._diagnostics.append(SkillDiagnostic(manifest_path(self.root), str(exc)))
                logger.error("activation manifest is unusable: %s", exc)

        if not self.root.is_dir():
            logger.debug("skills directory %s does not exist", self.root)
            return ()
        for entry in sorted(self.root.iterdir(), key=lambda path: path.name):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            if self._only is not None and entry.name not in self._only:
                continue
            try:
                skill = _read_skill(entry)
            except SkillError as exc:
                self._diagnostics.append(SkillDiagnostic(exc.path, exc.message))
                logger.warning("skipping skill at %s: %s", exc.path, exc.message)
                continue
            if skill is None:
                continue  # a folder without a SKILL.md is not a skill
            served = self._apply_activation(skill)
            if served is None:
                continue
            existing = self._skills.get(served.name)
            if existing is not None:
                message = (
                    f"duplicate skill name {served.name!r}; already loaded from "
                    f"{existing.path}"
                )
                self._diagnostics.append(SkillDiagnostic(entry, message))
                logger.warning("skipping skill at %s: %s", entry, message)
                continue
            self._skills[served.name] = served
        return self.skills()

    def skills(self) -> tuple[Skill, ...]:
        """The current catalogue, sorted by name (scans once on first use)."""
        if not self._loaded:
            self.refresh()
        return tuple(self._skills[name] for name in sorted(self._skills))

    def has_skills(self) -> bool:
        return bool(self.skills())

    def names(self) -> list[str]:
        return [skill.name for skill in self.skills()]

    def diagnostics(self) -> tuple[SkillDiagnostic, ...]:
        """Folders that looked like skills but were skipped, for logging/ops."""
        if not self._loaded:
            self.refresh()
        return tuple(self._diagnostics)

    def get(self, name: str) -> Skill | None:
        """Load one skill, re-reading its folder (and activation state)."""
        key = (name or "").strip()
        if not key:
            return None
        if self._only is not None and key not in self._only:
            return None
        if not self._loaded:
            self.refresh()
        else:
            # A manifest or snapshot may have changed since the last scan.
            self._strict = self._use_activation()
            if self._strict:
                try:
                    self._manifest = load_manifest(self.root) or {}
                except ActivationError:
                    # Fail closed: a manifest that cannot be read vouches for
                    # nothing, so nothing is served.
                    self._manifest = {}
        directory = self.root / key
        if directory.is_dir():
            try:
                skill = _read_skill(directory)
            except SkillError as exc:
                # A skill that has become invalid must stop being served, not
                # fall back to the copy from the last scan.
                self._diagnostics.append(SkillDiagnostic(exc.path, exc.message))
                logger.warning("skill %r is not loadable: %s", key, exc.message)
                self._skills.pop(key, None)
                return None
            if skill is None:
                self._skills.pop(key, None)
                return None
            served = self._apply_activation(skill)
            if served is None:
                self._skills.pop(key, None)
                return None
            self._skills[served.name] = served
            return served
        # The folder is gone: drop a stale entry instead of serving it forever.
        self._skills.pop(key, None)
        return None


def default_skill_registry() -> SkillRegistry:
    """A registry over the configured watched directory (scans on first use)."""
    return SkillRegistry()


__all__ = [
    "DEFAULT_SKILLS_DIR",
    "GOLDEN_FILENAME",
    "ACTIVATION_FILENAME",
    "MAX_DESCRIPTION_CHARS",
    "SKILL_FILENAME",
    "Skill",
    "SkillDiagnostic",
    "SkillError",
    "SkillRegistry",
    "default_skill_registry",
    "manifest_path",
    "default_skills_root",
    "parse_front_matter",
]
