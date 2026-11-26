"""Golden sets: the small question list that ships with a skill.

Each skill may carry a ``golden.json`` beside its ``SKILL.md``::

    {
      "skill": "granular-synthesis",
      "questions": [
        {
          "id": "gs-001",
          "question": "How do I build a granular sampler in Pd?",
          "expects": ["grain"],
          "notes": "What a good answer does, for a judge or reviewer."
        }
      ]
    }

The set exists so a skill can be measured on the questions it is supposed to
improve — TASK-5.15 gates activation of a skill version on its own slice.
``expects`` is a deliberately cheap signal (every listed term should appear in
a good answer, case-insensitively) rather than a wording test; ``notes`` is
where the human judgement lives.

Validation happens on read, so a schema slip fails loudly instead of producing
an eval slice that quietly measures nothing. The file is optional: a skill
without a golden set can still be loaded, it just cannot be gated yet.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import GOLDEN_FILENAME, Skill, SkillError

#: Small on purpose: this is a smoke slice per skill, not the full eval set.
MIN_QUESTIONS = 3
MAX_QUESTIONS = 12


@dataclass(frozen=True)
class GoldenQuestion:
    """One question a skill should improve, with what a good answer contains."""

    id: str
    question: str
    expects: tuple[str, ...] = ()
    notes: str = ""


@dataclass(frozen=True)
class GoldenSet:
    """The questions shipped with one skill."""

    skill: str
    questions: tuple[GoldenQuestion, ...]

    def __len__(self) -> int:
        return len(self.questions)

    def __iter__(self):
        return iter(self.questions)


def _fail(path: Path, message: str) -> SkillError:
    return SkillError(path, f"invalid golden set: {message}")


def _parse(path: Path, raw: Any, expected_skill: str) -> GoldenSet:
    if not isinstance(raw, dict):
        raise _fail(path, "expected a JSON object with 'skill' and 'questions'")
    skill = raw.get("skill")
    if not isinstance(skill, str) or not skill.strip():
        raise _fail(path, "missing a 'skill' name")
    if skill != expected_skill:
        raise _fail(
            path,
            f"names skill {skill!r} but lives in the {expected_skill!r} skill",
        )
    questions = raw.get("questions")
    if not isinstance(questions, list):
        raise _fail(path, "missing a 'questions' list")
    if not MIN_QUESTIONS <= len(questions) <= MAX_QUESTIONS:
        raise _fail(
            path,
            f"has {len(questions)} questions; keep it between {MIN_QUESTIONS} "
            f"and {MAX_QUESTIONS}",
        )

    parsed: list[GoldenQuestion] = []
    seen: set[str] = set()
    for index, item in enumerate(questions, start=1):
        if not isinstance(item, dict):
            raise _fail(path, f"question {index} is not an object")
        identifier = item.get("id")
        if not isinstance(identifier, str) or not identifier.strip():
            raise _fail(path, f"question {index} has no 'id'")
        identifier = identifier.strip()
        if identifier in seen:
            raise _fail(path, f"duplicate question id {identifier!r}")
        seen.add(identifier)
        question = item.get("question")
        if not isinstance(question, str) or len(question.strip()) < 8:
            raise _fail(path, f"{identifier} has no usable 'question'")
        expects = item.get("expects", [])
        if not isinstance(expects, list) or not all(
            isinstance(term, str) and term.strip() for term in expects
        ):
            raise _fail(path, f"{identifier}: 'expects' must be a list of non-empty strings")
        notes = item.get("notes", "")
        if not isinstance(notes, str):
            raise _fail(path, f"{identifier}: 'notes' must be a string")
        parsed.append(
            GoldenQuestion(
                id=identifier,
                question=question.strip(),
                expects=tuple(term.strip() for term in expects),
                notes=notes.strip(),
            )
        )
    return GoldenSet(skill=skill, questions=tuple(parsed))


def read_golden_set(skill: Skill) -> GoldenSet | None:
    """Load and validate one skill's golden set, or ``None`` when it has none."""
    path = skill.path / GOLDEN_FILENAME
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise _fail(path, f"cannot be read: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise _fail(path, f"is not valid JSON: {exc}") from exc
    return _parse(path, raw, skill.name)


__all__ = [
    "GOLDEN_FILENAME",
    "MAX_QUESTIONS",
    "MIN_QUESTIONS",
    "GoldenQuestion",
    "GoldenSet",
    "read_golden_set",
]
