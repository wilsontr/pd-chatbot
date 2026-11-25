"""The ``load_skill`` tool: progressive disclosure of the installed skills.

The model must not carry every skill's instructions in its context. The tool
description therefore contains only the catalogue — one line per skill with its
name and trigger description — and the instructions arrive as the tool result
only when the model asks for that skill by name.

The tool owns its ``SkillRegistry`` (rather than sharing a module-level one) so
concurrent requests cannot steal each other's refreshed catalogue. ``refresh()``
is called by the loop at the start of each turn, which is what makes a skill
added to the watched directory appear mid-session; ``run()`` re-reads the skill
folder from disk, so an edited ``SKILL.md`` is served without a restart.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..skills import SkillRegistry, default_skill_registry
from . import ToolOutcome, ToolSpec

#: The full instructions are text in the model's context, so cap them. A skill
#: that needs more should split into bundled reference files the instructions
#: point at (a later subtask adds a tool to read those).
MAX_INSTRUCTIONS_CHARS = 60_000

LOAD_SKILL_DESCRIPTION = (
    "Load one skill's full instructions. A skill is a playbook for a particular "
    "kind of request; below is the catalogue, which shows only each skill's name "
    "and a one-line description. When a request is covered by one of these "
    "descriptions, call this tool with that skill's name before starting the work, "
    "then follow the instructions it returns. Do not call it for a request that no "
    "description covers, and do not tell the user about the skill system.\n\n"
    "Available skills:\n{catalogue}"
)

LOAD_SKILL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": (
                "The skill's name exactly as listed in the tool description, "
                "e.g. 'granular-synthesis'."
            ),
        },
    },
    "required": ["name"],
    "additionalProperties": False,
}

_NO_SKILLS = "- none installed"


class LoadSkillTool:
    """Expose the skill catalogue, and load one skill's instructions on demand."""

    def __init__(self, registry: SkillRegistry | None = None):
        self.registry = registry if registry is not None else default_skill_registry()
        self.registry.refresh()

    def refresh(self) -> None:
        """Re-read the watched directory (called by the loop once per turn)."""
        self.registry.refresh()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="load_skill",
            description=LOAD_SKILL_DESCRIPTION.format(catalogue=self._catalogue()),
            input_schema=LOAD_SKILL_SCHEMA,
        )

    def _catalogue(self) -> str:
        skills = self.registry.skills()
        if not skills:
            return _NO_SKILLS
        return "\n".join(f"- {skill.name}: {skill.description}" for skill in skills)

    def _recovery(self, problem: str) -> str:
        names = self.registry.names()
        if not names:
            return (
                f"{problem} No skills are installed right now, so do not retry this "
                "tool; answer with the tools you already have."
            )
        return (
            f"{problem} Installed skills: {', '.join(names)}. Call load_skill again "
            "with one of those names, spelled exactly."
        )

    def run(self, arguments: Mapping[str, Any]) -> ToolOutcome:
        name = str(arguments.get("name") or "").strip()
        if not name:
            return ToolOutcome(
                content=self._recovery("load_skill needs the 'name' of a skill."),
                summary="load_skill called without a skill name",
                ok=False,
            )

        skill = self.registry.get(name)
        if skill is None:
            return ToolOutcome(
                content=self._recovery(f"There is no skill named {name!r}."),
                summary=f"unknown skill {name!r}",
                ok=False,
            )

        instructions = skill.instructions
        truncated = len(instructions) > MAX_INSTRUCTIONS_CHARS
        if truncated:
            instructions = (
                instructions[:MAX_INSTRUCTIONS_CHARS].rstrip()
                + "\n\n[Instructions truncated: this skill's SKILL.md is longer than "
                "the tool returns. Follow what is above and rely on your own tools for "
                "the rest.]"
            )

        parts = [
            f"# Skill: {skill.name} (version {skill.version})",
            "",
            instructions,
            "",
        ]
        if skill.files:
            parts.append("Files bundled with this skill (paths are relative to the skill folder):")
            parts.extend(f"- {relative}" for relative in skill.files)
        else:
            parts.append("This skill has no bundled files.")
        if skill.declared_version is None:
            parts.append(
                f"\nThis version is a content digest of the skill's files ({skill.digest[:12]})."
            )

        return ToolOutcome(
            content="\n".join(parts),
            summary=f"loaded skill {skill.name} (v{skill.version})",
            ok=True,
            data={
                # The loop and the run log read this key: it is the record of
                # which skill version this run actually loaded.
                "loaded_skill": skill.to_record(),
                "skill_files": list(skill.files),
                "instructions_chars": len(skill.instructions),
                "truncated": truncated,
            },
        )


def skill_tools(registry: SkillRegistry | None = None) -> list[LoadSkillTool]:
    """The skill tools a run starts with."""
    return [LoadSkillTool(registry)]
