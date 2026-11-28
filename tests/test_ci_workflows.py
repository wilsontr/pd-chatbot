"""The CI jobs that cover the agent's execution path and its skills (TASK-5.35-5.38).

These pin what each workflow runs and when, so a later edit cannot quietly drop
the sandbox tests, the patch-eval gate, the activation check on main, or the
rule that a push never overwrites the multi-trial baseline.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"


def load(name: str) -> dict:
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    data["on"] = data.pop(True, data.get("on"))  # YAML 1.1 reads `on:` as True
    return data


def steps(workflow: dict) -> list[dict]:
    return [s for job in workflow["jobs"].values() for s in job["steps"]]


def run_text(workflow: dict) -> str:
    return "\n".join(s.get("run", "") for s in steps(workflow))


AGENT_PATHS = {"agent/**", "sandbox/**", "skills/**", "evaluation/patch_eval.py"}


class TestSandboxJob:
    def test_runs_every_sandbox_test_and_requires_the_image(self):
        wf = load("sandbox.yml")
        assert "docker build -t pd-sandbox sandbox/" in run_text(wf)
        test = next(s for s in steps(wf) if "python -m pytest" in s.get("run", ""))
        assert "-m sandbox" in test["run"]
        assert test["env"]["PD_SANDBOX_REQUIRED"] == "1"

    @pytest.mark.parametrize("event", ["pull_request", "push"])
    def test_triggers_on_the_agent_the_patch_tools_the_sandbox_and_the_skills(self, event):
        assert AGENT_PATHS <= set(load("sandbox.yml")["on"][event]["paths"])

    def test_no_test_file_keeps_its_own_image_check(self):
        own = [
            p.name for p in (ROOT / "tests").glob("test_*.py")
            if re.search(r'"docker",\s*"image",\s*"inspect"', p.read_text(encoding="utf-8"))
        ]
        assert own == [], "use tests.needs_sandbox so `pytest -m sandbox` selects them"


class TestPatchEvalJob:
    def test_runs_the_with_execution_arm_gated_in_the_sandbox(self):
        wf = load("patch-eval.yml")
        text = run_text(wf)
        assert "docker build -t pd-sandbox sandbox/" in text
        assert re.search(r"evaluation\.patch_eval\s.*--arm with", text, re.S)
        assert "--gate" in text

    def test_triggers_on_the_agent_and_states_its_cost(self):
        wf = load("patch-eval.yml")
        assert AGENT_PATHS | {"evaluation/gates.py"} <= set(wf["on"]["pull_request"]["paths"])
        assert re.search(r"Cost per run:.*\$0\.\d+", (WORKFLOWS / "patch-eval.yml").read_text(),
                         re.S)


class TestSkillsCheck:
    def test_verifies_the_manifest_on_main_and_on_pull_requests(self):
        wf = load("skills-check.yml")
        assert wf["on"]["push"]["branches"] == ["main"]
        assert "skills/**" in wf["on"]["push"]["paths"]
        assert "pull_request" in wf["on"]
        assert "python -m evaluation.skills --verify" in run_text(wf)

    def test_a_manual_gate_run_commits_its_evidence(self):
        wf = load("skills.yml")
        commit = next(s for s in steps(wf) if "git commit" in s.get("run", ""))
        assert commit["if"].startswith("always()")
        assert "github.ref_name" in commit["env"]["TARGET"]


class TestEvalReports:
    def test_a_push_writes_the_routine_report_and_only_3_trials_write_the_baseline(self):
        wf = load("eval.yml")
        choose = next(s for s in steps(wf) if s.get("id") == "report")
        assert '"$TRIALS" -ge 3' in choose["run"]
        assert "docs/EVAL_BASELINE.md" in choose["run"]
        full = next(s for s in steps(wf) if "--all" in s.get("run", ""))
        assert "steps.report.outputs.path" in full["run"]
        assert "docs/EVAL.md" not in full["run"]


def test_the_skill_gate_pools_three_trials():
    """TASK-5.40: one trial of a small slice is mostly judge noise."""
    gate = next(s for s in steps(load("skills.yml")) if "evaluation.skills" in s.get("run", ""))
    assert "--trials 3" in gate["run"] and "--gate --activate" in gate["run"]


def test_the_skill_gate_rebases_before_pushing_its_activation():
    """A gate run lasts long enough for main to move; a rejected push loses it."""
    commit = next(s for s in steps(load("skills.yml")) if "git commit" in s.get("run", ""))
    assert "git cherry-pick" in commit["run"] and "for attempt" in commit["run"]
