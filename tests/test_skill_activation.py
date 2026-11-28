"""Skill activation, the per-skill gate and its reporting (TASK-5.15).

The gate has to do three things and no more: run each changed skill's slice, keep
a failed version off the serving path while the previous one keeps serving, and
show per-skill results in the report. These tests cover each with a temporary
skills directory and stubbed answer/judge functions, so they need no API key.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent.skills import SkillRegistry
from agent.skills.activation import (
    ActivationEntry,
    ActivationError,
    activated_root,
    load_manifest,
    manifest_path,
    prune_snapshots,
    skill_digest,
    snapshot_path,
    store_snapshot,
    write_manifest,
)
from evaluation import skills as skill_eval
from evaluation.gates import (
    SKILL_THRESHOLDS,
    check_skill_thresholds,
    format_skill_thresholds,
    skills_summary_markdown,
)

CHUNK = {
    "id": "parent-1",
    "heading_path": "osc~",
    "text": "osc~ is a cosine oscillator.",
    "url": "https://example.test/osc~",
    "source": "iem_reference",
    "content_type": "object_reference",
    "object_name": "osc~",
}

SCORE_KEYS = ("faithfulness", "relevance", "citation_correctness")

NAME = "example-skill"


def write_skill(
    root: Path,
    name: str = NAME,
    *,
    body: str = "Do the thing carefully.",
    questions: int = 3,
) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A test skill used by the activation tests.\n---\n\n"
        f"{body}\n",
        encoding="utf-8",
    )
    (directory / "golden.json").write_text(json.dumps({
        "skill": name,
        "questions": [
            {
                "id": f"{name}-{index}",
                "question": f"Question {index} about Pd?",
                "notes": "What a good answer does.",
            }
            for index in range(1, questions + 1)
        ],
    }), encoding="utf-8")
    return directory


def baseline(root: Path, only: list[str] | None = None) -> list[str]:
    return skill_eval.activate_baseline(
        root=root,
        only=only,
        activated_at="2026-11-26T00:00:00Z",
    )


# --- Manifest and snapshots --------------------------------------------------


class TestManifest:
    def test_no_manifest_is_a_permissive_directory(self, tmp_path):
        assert load_manifest(tmp_path) is None
        write_skill(tmp_path)
        assert SkillRegistry(tmp_path).strict is False

    def test_manifest_round_trips(self, tmp_path):
        write_manifest(tmp_path, {
            NAME: ActivationEntry(
                version="1.2.3",
                digest="a" * 64,
                activated_at="2026-11-26T00:00:00Z",
                activated_by="tester",
                metrics={"faithfulness_mean": 4.8},
            )
        })
        entries = load_manifest(tmp_path)
        assert entries is not None
        assert entries[NAME].version == "1.2.3"
        assert entries[NAME].digest == "a" * 64
        assert entries[NAME].metrics["faithfulness_mean"] == 4.8

    def test_an_entry_without_a_digest_is_invalid(self, tmp_path):
        manifest_path(tmp_path).write_text(
            json.dumps({"version": 1, "skills": {NAME: {"version": "1"}}}),
            encoding="utf-8",
        )
        with pytest.raises(ActivationError, match="digest"):
            load_manifest(tmp_path)

    def test_malformed_json_is_reported(self, tmp_path):
        manifest_path(tmp_path).write_text("{not json", encoding="utf-8")
        with pytest.raises(ActivationError, match="not valid JSON"):
            load_manifest(tmp_path)

    def test_a_snapshot_path_cannot_escape_the_skills_directory(self, tmp_path):
        with pytest.raises(ActivationError):
            snapshot_path(tmp_path, NAME, "../../etc")


class TestStrictServing:
    def test_only_the_activated_version_is_served(self, tmp_path):
        directory = write_skill(tmp_path)
        baseline(tmp_path)
        (directory / "SKILL.md").write_text(
            (directory / "SKILL.md").read_text().replace("Do the thing", "EDITED IN PLACE")
        )

        strict = SkillRegistry(tmp_path)
        skill = strict.get(NAME)

        assert skill is not None
        assert "EDITED IN PLACE" not in skill.instructions
        assert skill.version == load_manifest(tmp_path)[NAME].version

    def test_the_previous_version_stays_live_after_an_ungated_edit(self, tmp_path):
        directory = write_skill(tmp_path)
        original = SkillRegistry(tmp_path).get(NAME).version
        baseline(tmp_path)

        (directory / "SKILL.md").write_text(
            (directory / "SKILL.md").read_text().replace(
                "Do the thing carefully.", "A completely different instruction."
            )
        )
        edited = SkillRegistry(tmp_path, activation=False).get(NAME)

        served = SkillRegistry(tmp_path).get(NAME)
        assert edited is not None and edited.version != original
        assert served is not None and served.version == original
        assert "A completely different instruction" not in served.instructions

    def test_a_changed_version_without_a_snapshot_is_not_served(self, tmp_path):
        directory = write_skill(tmp_path)
        baseline(tmp_path)
        # Drop the snapshot that the manifest expects, then edit the working copy.
        for path in sorted(activated_root(tmp_path).rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        (directory / "SKILL.md").write_text(
            (directory / "SKILL.md").read_text().replace("Do the thing", "EDITED")
        )

        registry = SkillRegistry(tmp_path)

        assert registry.names() == []
        assert any("no snapshot" in d.message for d in registry.diagnostics())

    def test_a_skill_missing_from_the_manifest_is_not_served(self, tmp_path):
        write_skill(tmp_path, name="first")
        baseline(tmp_path, only=["first"])
        write_skill(tmp_path, name="second")

        registry = SkillRegistry(tmp_path)

        assert registry.names() == ["first"]
        assert any("not activated" in d.message for d in registry.diagnostics())

    def test_a_corrupt_snapshot_is_not_served(self, tmp_path):
        directory = write_skill(tmp_path)
        baseline(tmp_path)
        entry = load_manifest(tmp_path)[NAME]
        snapshot = snapshot_path(tmp_path, NAME, entry.digest)
        (snapshot / "SKILL.md").write_text("tampered", encoding="utf-8")
        (directory / "SKILL.md").write_text(
            (directory / "SKILL.md").read_text().replace("Do the thing", "EDITED")
        )

        assert SkillRegistry(tmp_path).names() == []

    def test_an_invalid_manifest_fails_closed(self, tmp_path):
        write_skill(tmp_path)
        manifest_path(tmp_path).write_text("{broken", encoding="utf-8")

        registry = SkillRegistry(tmp_path)

        assert registry.names() == []
        assert any("not valid JSON" in d.message for d in registry.diagnostics())

    def test_activation_can_be_disabled_for_local_work(self, tmp_path, monkeypatch):
        directory = write_skill(tmp_path)
        baseline(tmp_path)
        (directory / "SKILL.md").write_text(
            (directory / "SKILL.md").read_text().replace("Do the thing", "LOCAL EDIT")
        )

        monkeypatch.setenv("SKILLS_ACTIVATION", "off")
        registry = SkillRegistry(tmp_path)

        served = registry.get(NAME)
        assert served is not None and "LOCAL EDIT" in served.instructions

    def test_only_filters_the_catalogue(self, tmp_path):
        write_skill(tmp_path, name="first")
        write_skill(tmp_path, name="second")
        registry = SkillRegistry(tmp_path, activation=False, only={"second"})
        assert registry.names() == ["second"]
        assert registry.get("first") is None

    def test_activating_a_new_version_takes_effect_on_the_next_scan(self, tmp_path):
        directory = write_skill(tmp_path)
        baseline(tmp_path)
        (directory / "SKILL.md").write_text(
            (directory / "SKILL.md").read_text().replace("Do the thing", "VERSION TWO")
        )
        edited = SkillRegistry(tmp_path, activation=False).get(NAME)

        # Simulate CI activating the edited content.
        result = {
            "skill": NAME,
            "version": edited.version,
            "digest": edited.digest,
            "path": str(edited.path),
        }
        skill_eval.activate_result(
            result, root=tmp_path, actor="tester", activated_at="2026-11-26T01:00:00Z"
        )

        registry = SkillRegistry(tmp_path)
        served = registry.get(NAME)
        assert served is not None
        assert "VERSION TWO" in served.instructions


# --- Activation writers ------------------------------------------------------


class TestActivationWriters:
    def test_baseline_pins_and_snapshots_every_skill(self, tmp_path):
        write_skill(tmp_path, name="first")
        write_skill(tmp_path, name="second")

        activated = baseline(tmp_path)

        assert sorted(activated) == ["first", "second"]
        entries = load_manifest(tmp_path)
        assert set(entries) == {"first", "second"}
        for name in entries:
            assert entries[name].metrics["baseline"] is True
            assert snapshot_path(tmp_path, name, entries[name].digest).is_dir()

    def test_activate_result_is_idempotent_and_records_metrics(self, tmp_path):
        directory = write_skill(tmp_path)
        skill = SkillRegistry(tmp_path, activation=False).get(NAME)
        result = {
            "skill": NAME,
            "version": skill.version,
            "digest": skill.digest,
            "path": str(directory),
            "questions": 3,
            "judged": 3,
            "task_success_rate": 1.0,
            "load_rate": 1.0,
            "faithfulness_mean": 4.7,
            "relevance_mean": 4.9,
            "citation_correctness_mean": 4.6,
        }

        changed = skill_eval.activate_result(
            result, root=tmp_path, actor="tester", activated_at="2026-11-26T00:00:00Z"
        )
        again = skill_eval.activate_result(
            result, root=tmp_path, actor="tester", activated_at="2026-11-26T00:00:00Z"
        )

        assert changed is True
        assert again is False
        entry = load_manifest(tmp_path)[NAME]
        assert entry.version == skill.version
        assert entry.digest == skill.digest
        assert entry.activated_by == "tester"
        assert entry.metrics["faithfulness_mean"] == 4.7

    def test_store_snapshot_rejects_content_that_does_not_match_the_digest(self, tmp_path):
        directory = write_skill(tmp_path)
        with pytest.raises(ActivationError, match="does not match digest"):
            store_snapshot(tmp_path, NAME, "b" * 64, directory)

    def test_prune_snapshots_keeps_the_newest(self, tmp_path):
        directory = write_skill(tmp_path)
        digests = []
        for index in range(3):
            (directory / "SKILL.md").write_text(
                (directory / "SKILL.md").read_text() + f"\nRevision {index}.\n",
                encoding="utf-8",
            )
            digest = skill_digest(directory)
            digests.append(digest)
            store_snapshot(tmp_path, NAME, digest, directory)

        removed = prune_snapshots(tmp_path, NAME, keep=2)

        remaining = sorted(path.name for path in (activated_root(tmp_path) / NAME).iterdir())
        assert len(remaining) == 2
        assert len(removed) == 1


# --- The gate ----------------------------------------------------------------


def result(**overrides: Any) -> dict[str, Any]:
    base = {
        "skill": NAME,
        "version": "v1",
        "questions": 5,
        "judged": 5,
        "faithfulness_mean": 4.7,
        "relevance_mean": 4.8,
        "citation_correctness_mean": 4.6,
        "load_rate": 0.8,
        "task_success_rate": 0.8,
    }
    base.update(overrides)
    return base


class TestSkillGate:
    def test_a_clean_slice_passes(self):
        assert check_skill_thresholds(result()) == []

    @pytest.mark.parametrize(
        "overrides, message",
        [
            ({"relevance_mean": 4.2}, "relevance"),
            ({"faithfulness_mean": 3.0}, "faithfulness"),
            ({"citation_correctness_mean": 3.5}, "citation_correctness"),
            ({"load_rate": 0.2}, "loaded for 20%"),
            ({"judged": 0, "load_rate": 0.0}, "could be judged"),
            ({"faithfulness_mean": None}, "no usable score"),
        ],
    )
    def test_each_failure_is_named(self, overrides, message):
        failures = check_skill_thresholds(result(**overrides))
        assert failures
        assert any(message in failure for failure in failures)

    def test_thresholds_are_documented(self):
        text = format_skill_thresholds()
        for dimension in SCORE_KEYS:
            assert dimension in text
        assert "load rate" in text
        assert SKILL_THRESHOLDS["min_load_rate"] == 0.5


# --- Running a slice ---------------------------------------------------------


def fake_answer(*, loaded: bool = True, chunks: list[dict] | None = None, text: str = "An answer."):
    return SimpleNamespace(
        answer=text,
        chunks=[CHUNK] if chunks is None else chunks,
        loaded_skills={NAME: "v1"} if loaded else {},
        usage={"input": 100, "output": 20, "cache_read": 0, "total": 120},
        tool_calls=2,
        turns=2,
        retrieval_calls=1,
        tool_errors=0,
        recovered_errors=0,
        unresolved_errors=0,
    )


def fake_judge(score: int = 5):
    def judge(question, answer, chunks, usage_sink):
        usage_sink["judge-model"] = {"input_tokens": 10, "output_tokens": 5}
        return {
            dimension: {"score": score, "explanation": ""}
            for dimension in SCORE_KEYS
        }

    return judge


class TestRunSkillEval:
    def test_a_slice_is_answered_judged_and_summarised(self, tmp_path):
        write_skill(tmp_path)
        seen: list[str] = []

        def answer(question, history):
            seen.append(question)
            return fake_answer()

        summary = skill_eval.run_skill_eval(
            NAME,
            root=tmp_path,
            answer_fn=answer,
            judge_fn=fake_judge(),
        )

        assert seen == [f"Question {index} about Pd?" for index in (1, 2, 3)]
        assert summary["questions"] == 3
        assert summary["judged"] == 3
        assert summary["faithfulness_mean"] == 5.0
        assert summary["task_success_rate"] == 1.0
        assert summary["load_rate"] == 1.0
        assert summary["skill_loads"] == 3
        assert summary["avg_tool_calls"] == 2.0
        assert summary["avg_cost_usd"] is not None
        assert summary["judge_cost_usd"] is not None
        assert summary["digest"]

    def test_a_skill_that_is_never_loaded_fails_the_load_floor(self, tmp_path):
        write_skill(tmp_path)
        summary = skill_eval.run_skill_eval(
            NAME,
            root=tmp_path,
            answer_fn=lambda question, history: fake_answer(loaded=False),
            judge_fn=fake_judge(),
        )
        assert summary["load_rate"] == 0.0
        assert any("loaded for 0%" in failure for failure in check_skill_thresholds(summary))

    def test_a_failed_run_and_an_empty_retrieval_still_count(self, tmp_path):
        write_skill(tmp_path)
        calls = {"n": 0}

        def answer(question, history):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("provider down")
            if calls["n"] == 2:
                return fake_answer(chunks=[])
            return fake_answer()

        summary = skill_eval.run_skill_eval(
            NAME, root=tmp_path, answer_fn=answer, judge_fn=fake_judge()
        )

        assert summary["questions"] == 3
        assert summary["judged"] == 1
        assert summary["run_errors"] == 1
        assert summary["skipped_no_context"] == 1
        assert summary["task_success_rate"] == pytest.approx(1 / 3, abs=1e-4)
        assert summary["load_rate"] == pytest.approx(2 / 3, abs=1e-4)

    def test_an_low_judge_score_fails_the_slice(self, tmp_path):
        write_skill(tmp_path)
        summary = skill_eval.run_skill_eval(
            NAME,
            root=tmp_path,
            answer_fn=lambda question, history: fake_answer(),
            judge_fn=fake_judge(score=3),
        )
        failures = check_skill_thresholds(summary)
        assert any("faithfulness 3.00 < 4" in failure for failure in failures)

    def test_a_skill_without_a_golden_set_cannot_be_evaluated(self, tmp_path):
        directory = write_skill(tmp_path)
        (directory / "golden.json").unlink()
        with pytest.raises(ValueError, match="no golden set"):
            skill_eval.run_skill_eval(NAME, root=tmp_path)


# --- CLI ---------------------------------------------------------------------


class TestCli:
    def test_passing_slice_is_activated_and_written_to_the_manifest(self, tmp_path, monkeypatch):
        write_skill(tmp_path)
        skill = SkillRegistry(tmp_path, activation=False).get(NAME)
        passing = result(
            skill=NAME,
            version=skill.version,
            digest=skill.digest,
            path=str(skill.path),
        )
        monkeypatch.setattr(skill_eval, "run_skill_eval", lambda *a, **k: passing)

        report = tmp_path / "SKILLS.md"
        code = skill_eval.main([
            "--skills", NAME, "--skills-root", str(tmp_path),
            "--gate", "--activate", "--report", str(report), "--quiet",
        ])

        assert code == 0
        assert load_manifest(tmp_path)[NAME].digest == skill.digest
        assert snapshot_path(tmp_path, NAME, skill.digest).is_dir()
        assert NAME in report.read_text()

    def test_failing_slice_is_not_activated_and_leaves_the_previous_version(
        self, tmp_path, monkeypatch
    ):
        write_skill(tmp_path)
        baseline(tmp_path)
        previous = load_manifest(tmp_path)[NAME]
        skill = SkillRegistry(tmp_path, activation=False).get(NAME)
        failing = result(
            skill=NAME,
            version=skill.version,
            digest=skill.digest,
            path=str(skill.path),
            relevance_mean=3.0,
        )
        monkeypatch.setattr(skill_eval, "run_skill_eval", lambda *a, **k: failing)

        code = skill_eval.main([
            "--skills", NAME, "--skills-root", str(tmp_path),
            "--gate", "--activate", "--quiet",
        ])

        assert code == 1
        assert load_manifest(tmp_path)[NAME].digest == previous.digest
        served = SkillRegistry(tmp_path).get(NAME)
        assert served is not None and served.digest == previous.digest

    def test_render_report_uses_the_manifest_without_evaluating(self, tmp_path):
        write_skill(tmp_path)
        baseline(tmp_path)
        report = tmp_path / "SKILLS.md"

        code = skill_eval.main([
            "--skills-root", str(tmp_path), "--render-report", str(report), "--quiet",
        ])

        assert code == 0
        text = report.read_text()
        assert NAME in text
        assert "baseline (not measured)" in text
        assert format_skill_thresholds() in text

    def test_eval_report_section_lists_activated_skills(self, tmp_path):
        write_skill(tmp_path)
        baseline(tmp_path)
        section = skills_summary_markdown(tmp_path)
        assert "## Skills (eval-gated)" in section
        assert NAME in section
        assert "baseline" in section

    def test_no_manifest_means_no_report_section(self, tmp_path):
        assert skills_summary_markdown(tmp_path) == ""

    def test_the_evaluation_report_carries_the_per_skill_section(self):
        from evaluation.evaluate import _build_report

        report = _build_report(
            {},
            {
                "total_entries": 0,
                "by_query_type": {},
                "by_difficulty": {},
                "multi_turn_count": 0,
                "entries_with_relevant_chunks": 0,
                "entries_with_reference_answer": 0,
            },
        )
        assert "## Skills (eval-gated)" in report
        assert "| Skill | Activated version |" in report


# --- CI evidence and the check on main (TASK-5.37) ---------------------------

CI_ENV = {
    "GITHUB_ACTIONS": "true",
    "GITHUB_RUN_ID": "4242",
    "GITHUB_SERVER_URL": "https://github.com",
    "GITHUB_REPOSITORY": "owner/repo",
    "GITHUB_WORKFLOW": skill_eval.GATE_WORKFLOW,
    "GITHUB_EVENT_NAME": "workflow_dispatch",
    "GITHUB_SHA": "abc123",
}


def _clear_ci(monkeypatch):
    for key in CI_ENV:
        monkeypatch.delenv(key, raising=False)


def _gated(root: Path, monkeypatch, name: str = NAME, *, ci: bool = True) -> None:
    directory = root / name
    skill = SkillRegistry(root, activation=False).get(name)
    _clear_ci(monkeypatch)
    if ci:
        for key, value in CI_ENV.items():
            monkeypatch.setenv(key, value)
    skill_eval.activate_result(
        {"skill": name, "version": skill.version, "digest": skill.digest,
         "path": str(directory), "questions": 3, "judged": 3},
        root=root, actor="tester",
    )


class TestCiEvidence:
    def test_outside_ci_there_is_none(self):
        assert skill_eval.ci_evidence({}) is None
        assert skill_eval.ci_evidence({"GITHUB_ACTIONS": "true"}) is None

    def test_in_ci_the_run_is_recorded(self, tmp_path, monkeypatch):
        write_skill(tmp_path)
        _gated(tmp_path, monkeypatch)
        ci = load_manifest(tmp_path)[NAME].metrics["ci"]
        assert ci == {
            "run_id": "4242", "run_url": "https://github.com/owner/repo/actions/runs/4242",
            "workflow": "Skill eval gate", "event": "workflow_dispatch", "commit": "abc123",
        }
        assert "[CI run](https://github.com/owner/repo/actions/runs/4242)" in (
            skill_eval.render_report(root=tmp_path))

    def test_the_gate_workflow_name_matches_skills_yml(self):
        workflow = Path(__file__).parent.parent / ".github" / "workflows" / "skills.yml"
        assert f"name: {skill_eval.GATE_WORKFLOW}\n" in workflow.read_text()


class TestVerify:
    def test_ci_gated_pins_pass(self, tmp_path, monkeypatch):
        write_skill(tmp_path)
        _gated(tmp_path, monkeypatch)
        assert skill_eval.verify_manifest(tmp_path) == ([], [])
        assert skill_eval.main(["--verify", "--skills-root", str(tmp_path)]) == 0

    def test_a_baseline_pin_fails(self, tmp_path):
        write_skill(tmp_path)
        baseline(tmp_path)
        failures, _ = skill_eval.verify_manifest(tmp_path)
        assert failures == [f"{NAME} {load_manifest(tmp_path)[NAME].version}: "
                            "pinned as an ungated baseline"]
        assert skill_eval.main(["--verify", "--skills-root", str(tmp_path)]) == 1

    def test_a_local_activation_fails(self, tmp_path, monkeypatch):
        write_skill(tmp_path)
        _gated(tmp_path, monkeypatch, ci=False)
        failures, _ = skill_eval.verify_manifest(tmp_path)
        assert len(failures) == 1 and "no CI-gated evidence (activated by tester)" in failures[0]

    def test_evidence_from_another_workflow_fails(self, tmp_path, monkeypatch):
        write_skill(tmp_path)
        _gated(tmp_path, monkeypatch)
        monkeypatch.setenv("GITHUB_WORKFLOW", "Evaluation")
        write_skill(tmp_path, "other")
        _gated_other = SkillRegistry(tmp_path, activation=False).get("other")
        skill_eval.activate_result(
            {"skill": "other", "version": _gated_other.version, "digest": _gated_other.digest,
             "path": str(tmp_path / "other")}, root=tmp_path)
        failures, _ = skill_eval.verify_manifest(tmp_path)
        assert len(failures) == 1 and failures[0].startswith("other ")

    def test_a_tampered_or_missing_snapshot_fails(self, tmp_path, monkeypatch):
        write_skill(tmp_path)
        _gated(tmp_path, monkeypatch)
        entry = load_manifest(tmp_path)[NAME]
        snap = snapshot_path(tmp_path, NAME, entry.digest)
        (snap / "SKILL.md").write_text("edited by hand\n", encoding="utf-8")
        failures, _ = skill_eval.verify_manifest(tmp_path)
        assert failures == [f"{NAME} {entry.version}: snapshot does not match its pinned digest"]
        import shutil
        shutil.rmtree(snap)
        failures, _ = skill_eval.verify_manifest(tmp_path)
        assert failures and "is missing" in failures[0]

    def test_an_ungated_working_edit_is_a_note_not_a_failure(self, tmp_path, monkeypatch):
        directory = write_skill(tmp_path)
        _gated(tmp_path, monkeypatch)
        (directory / "SKILL.md").write_text(
            (directory / "SKILL.md").read_text() + "More.\n", encoding="utf-8")
        failures, notes = skill_eval.verify_manifest(tmp_path)
        assert failures == [] and "snapshot serves" in notes[0]


class TestDeactivate:
    def test_deactivating_stops_serving_and_records_why(self, tmp_path, monkeypatch):
        write_skill(tmp_path)
        write_skill(tmp_path, "kept")
        baseline(tmp_path)
        _gated(tmp_path, monkeypatch, "kept")
        _clear_ci(monkeypatch)
        assert skill_eval.main([
            "--deactivate", NAME, "--reason", "fails relevance", "--skills-root", str(tmp_path),
        ]) == 0
        assert set(load_manifest(tmp_path)) == {"kept"}
        assert SkillRegistry(tmp_path).names() == ["kept"]
        raw = json.loads(manifest_path(tmp_path).read_text())
        assert raw["deactivated"][NAME]["reason"] == "fails relevance"
        failures, notes = skill_eval.verify_manifest(tmp_path)
        assert failures == [] and f"{NAME}: deactivated — fails relevance" in notes
        report = skill_eval.render_report(root=tmp_path)
        assert "## Deactivated" in report and "| deactivated |" in report

    def test_a_reason_is_required(self, tmp_path):
        write_skill(tmp_path)
        baseline(tmp_path)
        with pytest.raises(ActivationError):
            skill_eval.deactivate(NAME, "  ", root=tmp_path)

    def test_a_later_activation_clears_the_deactivation(self, tmp_path, monkeypatch):
        write_skill(tmp_path)
        baseline(tmp_path)
        skill_eval.deactivate(NAME, "flaky", root=tmp_path)
        _gated(tmp_path, monkeypatch)
        assert "deactivated" not in json.loads(manifest_path(tmp_path).read_text())
        assert skill_eval.verify_manifest(tmp_path) == ([], [])
