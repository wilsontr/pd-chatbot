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

import hashlib
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

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

_IGNORED_DIRS = frozenset({"__pycache__", "node_modules"})
_IGNORED_FILES = frozenset({".DS_Store"})

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


def _iter_skill_files(directory: Path) -> list[Path]:
    """Every content file under a skill folder, deterministically ordered."""
    files = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(directory)
        if any(part.startswith(".") or part in _IGNORED_DIRS for part in relative.parts):
            continue
        if relative.name in _IGNORED_FILES:
            continue
        files.append(relative)
    return files


def _digest(directory: Path, files: list[Path]) -> str:
    """Hash the skill's files (paths and contents) into a stable content identity."""
    hasher = hashlib.sha256()
    for relative in files:
        hasher.update(relative.as_posix().encode("utf-8"))
        hasher.update(b"\0")
        try:
            with (directory / relative).open("rb") as handle:
                for block in iter(lambda: handle.read(65536), b""):
                    hasher.update(block)
        except OSError:  # a file vanished mid-scan; the next refresh will catch it
            continue
        hasher.update(b"\0")
    return hasher.hexdigest()


def _read_skill(directory: Path) -> Skill | None:
    """Read one folder into a ``Skill``, or ``None`` when it is not a skill folder.

    Raises ``SkillError`` for a folder that has a ``SKILL.md`` but cannot be
    served (bad name, missing description, empty instructions).
    """
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
    if name != directory.name:
        raise SkillError(
            directory,
            f"front-matter name {name!r} does not match its folder name "
            f"{directory.name!r}; they must be the same so the skill can be found",
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

    files = _iter_skill_files(directory)
    digest = _digest(directory, files)
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
    """

    def __init__(self, root: str | Path | None = None):
        self.root: Path = Path(root).expanduser() if root is not None else default_skills_root()
        self._skills: dict[str, Skill] = {}
        self._diagnostics: list[SkillDiagnostic] = []
        self._loaded = False

    # -- discovery -----------------------------------------------------------

    def refresh(self) -> tuple[Skill, ...]:
        """Re-read the watched directory and return the catalogue, sorted by name."""
        self._skills = {}
        self._diagnostics = []
        self._loaded = True
        if not self.root.is_dir():
            logger.debug("skills directory %s does not exist", self.root)
            return ()
        for entry in sorted(self.root.iterdir(), key=lambda path: path.name):
            if not entry.is_dir() or entry.name.startswith(".") or entry.name in _IGNORED_DIRS:
                continue
            try:
                skill = _read_skill(entry)
            except SkillError as exc:
                self._diagnostics.append(SkillDiagnostic(exc.path, exc.message))
                logger.warning("skipping skill at %s: %s", exc.path, exc.message)
                continue
            if skill is None:
                continue  # a folder without a SKILL.md is not a skill
            existing = self._skills.get(skill.name)
            if existing is not None:
                message = (
                    f"duplicate skill name {skill.name!r}; already loaded from {existing.path}"
                )
                self._diagnostics.append(SkillDiagnostic(entry, message))
                logger.warning("skipping skill at %s: %s", entry, message)
                continue
            self._skills[skill.name] = skill
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
        """Load one skill, re-reading its folder so edits apply immediately."""
        key = (name or "").strip()
        if not key:
            return None
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
            else:
                self._skills[skill.name] = skill
            return skill
        # The folder is gone: drop a stale entry instead of serving it forever.
        if not self._loaded:
            self.refresh()
        self._skills.pop(key, None)
        return None


def default_skill_registry() -> SkillRegistry:
    """A registry over the configured watched directory (scans on first use)."""
    return SkillRegistry()


__all__ = [
    "DEFAULT_SKILLS_DIR",
    "GOLDEN_FILENAME",
    "MAX_DESCRIPTION_CHARS",
    "SKILL_FILENAME",
    "Skill",
    "SkillDiagnostic",
    "SkillError",
    "SkillRegistry",
    "default_skill_registry",
    "default_skills_root",
    "parse_front_matter",
]
