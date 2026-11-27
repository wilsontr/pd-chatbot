"""Golden sets for skills: loader validation and the shipped Pd skills (TASK-5.14).

The acceptance criteria are content requirements — at least four skills with clear
trigger descriptions, each carrying a small golden set — so these tests check the
shipped ``skills/`` directory as well as the loader that TASK-5.15's eval gate
will use.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.skills import (
    GOLDEN_FILENAME,
    MAX_DESCRIPTION_CHARS,
    Skill,
    SkillError,
    SkillRegistry,
)
from agent.skills.golden import (
    MAX_QUESTIONS,
    MIN_QUESTIONS,
    read_golden_set,
)
from agent.tools.skills import LoadSkillTool

REPO_SKILLS = Path(__file__).resolve().parents[1] / "skills"

EXPECTED_SKILLS = {
    "abstractions-and-dollar-zero",
    "dsp-debugging",
    "granular-synthesis",
    "pd-flavors",
    "wavetables",
    "working-from-a-pd-file",
}


def write_skill(root: Path, name: str = "example-skill", *, body: str = "Do the thing.") -> Skill:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A skill used by the tests.\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return SkillRegistry(root).skills()[0]


def golden_payload(
    name: str = "example-skill",
    count: int = MIN_QUESTIONS,
    *,
    questions: list[dict] | None = None,
) -> dict:
    if questions is None:
        questions = [
            {
                "id": f"q-{index}",
                "question": f"Question number {index} about Pd?",
                "expects": ["term"],
                "notes": "What a good answer does.",
            }
            for index in range(1, count + 1)
        ]
    return {"skill": name, "questions": questions}


def attach_golden(skill: Skill, payload: object) -> None:
    (skill.path / GOLDEN_FILENAME).write_text(json.dumps(payload), encoding="utf-8")


class TestGoldenSetLoader:
    def test_a_valid_set_is_parsed(self, tmp_path):
        skill = write_skill(tmp_path)
        attach_golden(skill, golden_payload())

        golden = read_golden_set(skill)

        assert golden is not None
        assert golden.skill == "example-skill"
        assert len(golden) == MIN_QUESTIONS
        assert golden.questions[0].id == "q-1"
        assert golden.questions[0].question.startswith("Question number 1")
        assert golden.questions[0].expects == ("term",)
        assert golden.questions[0].notes

    def test_a_skill_without_a_golden_set_is_allowed(self, tmp_path):
        skill = write_skill(tmp_path)
        assert read_golden_set(skill) is None

    def test_the_skill_name_must_match_its_folder(self, tmp_path):
        skill = write_skill(tmp_path)
        attach_golden(skill, golden_payload(name="someone-else"))
        with pytest.raises(SkillError, match="names skill"):
            read_golden_set(skill)

    @pytest.mark.parametrize("count", [MIN_QUESTIONS - 1, MAX_QUESTIONS + 1])
    def test_the_question_count_is_bounded(self, tmp_path, count):
        skill = write_skill(tmp_path)
        attach_golden(skill, golden_payload(count=count))
        with pytest.raises(SkillError, match="questions"):
            read_golden_set(skill)

    def test_question_ids_must_be_unique(self, tmp_path):
        skill = write_skill(tmp_path)
        payload = golden_payload()
        payload["questions"][1]["id"] = payload["questions"][0]["id"]
        attach_golden(skill, payload)
        with pytest.raises(SkillError, match="duplicate question id"):
            read_golden_set(skill)

    @pytest.mark.parametrize(
        "override, message",
        [
            ({"question": "   "}, "no usable 'question'"),
            ({"id": ""}, "no 'id'"),
            ({"expects": "grain"}, "'expects' must be a list"),
            ({"expects": [""]}, "'expects' must be a list"),
            ({"notes": 5}, "'notes' must be a string"),
        ],
    )
    def test_malformed_questions_fail_loudly(self, tmp_path, override, message):
        skill = write_skill(tmp_path)
        payload = golden_payload()
        payload["questions"][0].update(override)
        attach_golden(skill, payload)
        with pytest.raises(SkillError, match=message):
            read_golden_set(skill)

    def test_a_missing_skill_field_is_rejected(self, tmp_path):
        skill = write_skill(tmp_path)
        attach_golden(skill, {"questions": golden_payload()["questions"]})
        with pytest.raises(SkillError, match="'skill'"):
            read_golden_set(skill)

    def test_invalid_json_is_reported_with_the_path(self, tmp_path):
        skill = write_skill(tmp_path)
        (skill.path / GOLDEN_FILENAME).write_text("{not json", encoding="utf-8")
        with pytest.raises(SkillError, match="not valid JSON"):
            read_golden_set(skill)


@pytest.fixture(scope="module")
def registry() -> SkillRegistry:
    """The real skills/ folder, scanned once for the content checks.

    Content checks read the working folders (``activation=False``): a skill edit
    on a pull request is legitimately ahead of its activation, and the shipped
    content is what these tests are about. Activation gating has its own tests in
    ``test_skill_activation.py``.
    """
    return SkillRegistry(REPO_SKILLS, activation=False)


class TestShippedSkills:
    """The content acceptance criteria, checked against the real skills folder."""

    def test_at_least_four_skills_are_installed(self, registry):
        assert len(registry.skills()) >= 4
        assert set(registry.names()) == EXPECTED_SKILLS

    def test_every_skill_loads_without_a_diagnostic(self, registry):
        assert registry.diagnostics() == ()
        for skill in registry.skills():
            assert skill.instructions.strip()
            assert skill.version

    def test_every_skill_has_a_one_line_trigger_description(self, registry):
        for skill in registry.skills():
            assert "\n" not in skill.description
            assert 20 <= len(skill.description) <= MAX_DESCRIPTION_CHARS
            assert skill.description.endswith(".")

    def test_every_skill_has_a_golden_set(self, registry):
        for skill in registry.skills():
            golden = read_golden_set(skill)
            assert golden is not None, f"{skill.name} has no {GOLDEN_FILENAME}"
            assert MIN_QUESTIONS <= len(golden) <= MAX_QUESTIONS

    def test_golden_question_ids_are_unique_across_skills(self, registry):
        ids: list[str] = []
        for skill in registry.skills():
            golden = read_golden_set(skill)
            ids.extend(question.id for question in golden)
        assert len(ids) == len(set(ids))

    def test_every_golden_question_is_a_real_question_with_a_note(self, registry):
        for skill in registry.skills():
            for question in read_golden_set(skill):
                assert "?" in question.question
                assert len(question.question) > 30
                assert question.notes, f"{question.id} has no notes for the judge"

    def test_the_tool_lists_every_skill_and_loads_each_one(self, registry):
        tool = LoadSkillTool(registry)
        description = tool.spec.description
        for skill in registry.skills():
            assert skill.name in description
            assert skill.description in description

            outcome = tool.run({"name": skill.name})
            assert outcome.ok is True
            assert outcome.data["loaded_skill"] == skill.to_record()
            # The instructions themselves are not part of the pre-load view.
            first_line = skill.instructions.splitlines()[0]
            if len(first_line) > 25:
                assert first_line not in description

    def test_the_golden_set_is_not_advertised_as_a_bundled_file(self, registry):
        for skill in registry.skills():
            assert GOLDEN_FILENAME not in skill.files

    def test_every_activated_digest_has_its_snapshot(self):
        """The deploy artifact the gate depends on is intact.

        Working folders may legitimately be ahead of the manifest (a pull request
        that changes a skill, before CI activates it); what must never be missing
        is the snapshot that keeps the activated version serving.
        """
        from agent.skills.activation import load_manifest, skill_digest, snapshot_path

        entries = load_manifest(REPO_SKILLS)
        assert entries, "skills/activation.json is missing"
        for name, entry in entries.items():
            snapshot = snapshot_path(REPO_SKILLS, name, entry.digest)
            assert snapshot.is_dir(), f"{name} has no snapshot for {entry.digest[:12]}"
            assert skill_digest(snapshot) == entry.digest
