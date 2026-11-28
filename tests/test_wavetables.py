"""TASK-5.28: wavetable patches that run cleanly and are silent.

The agent's failing p_14 patches all broke on the *fill* side: the table was never
written, so a correct read chain played zeros. These tests pin those shapes as
silent, check that every route the ``wavetables`` skill prescribes meets the p_14
spec in the sandbox, and check that the read-back the skill and the prompt rule
recommend tells a filled table from an empty one.
"""

from __future__ import annotations

import pytest

from agent.prompt import agent_system_prompt
from agent.skills import SkillRegistry
from agent.tools.patch import runnable_file
from evaluation.patch_eval import load_tasks, score_patch
from sandbox import run_patch
from tests.needs_sandbox import needs_sandbox
from tests.test_patch_eval import make

SPEC = next(t["spec"] for t in load_tasks() if t["id"] == "p_14")

PLAY_512 = ["phasor~ 220", "*~ 512", "tabread4~ w", "*~ 0.2", "dac~"]


def _play(first: int) -> str:
    a, b, c, d, e = range(first, first + 5)
    return f"{a}.0>{b}.0 {b}.0>{c}.0 {c}.0>{d}.0 {d}.0>{e}.0 {d}.0>{e}.1"


#: The agent's final patch from .tmp/verify_wavetable.json (revision 3), box for box:
#: no wire from [until] to [f], and the [0( that resets [f] also asks [until] for
#: zero iterations. Runs cleanly; the table stays zeros.
RECORDED = make(
    ["loadbang", "msg|0", "until", "f", "+ 1", "sel 512",
     "expr sin($f1 * 6.2831853 / 512)", "tabwrite w", "table w 512", *PLAY_512],
    "0.0>1.0 1.0>3.1 1.0>2.0 3.0>4.0 4.0>3.1 3.0>6.0 6.0>7.0 3.0>7.1 3.0>5.0 5.0>2.1 "
    + _play(9),
)

#: Control runs 2, 5 and 6: a sinesum message without the leading ``;`` and wired to
#: nothing, so it never reaches the table.
UNROUTED_SINESUM = make(
    ["loadbang", "msg|sinesum 512 1", "table w 515", "sig~ 220", "tabosc4~ w", "dac~"],
    "0.0>1.0 3.0>4.0 4.0>5.0 4.0>5.1",
)

#: Skill route 1: sinesum + [tabosc4~].
SINESUM_TABOSC = make(
    ["loadbang", "msg|; w sinesum 512 1", "table w 515", "sig~ 220", "tabosc4~ w",
     "*~ 0.2", "dac~"],
    "0.0>1.0 3.0>4.0 4.0>5.0 5.0>6.0 5.0>6.1",
)

#: Skill route 2: sinesum table read by [phasor~] -> [*~ N] -> [+~ 1] -> [tabread4~].
SINESUM_TABREAD = make(
    ["loadbang", "msg|; w sinesum 512 1", "table w 515",
     "phasor~ 220", "*~ 512", "+~ 1", "tabread4~ w", "*~ 0.2", "dac~"],
    "0.0>1.0 3.0>4.0 4.0>5.0 5.0>6.0 6.0>7.0 7.0>8.0 7.0>8.1",
)

#: Skill route 3: the counter loop with the count into [until], [until] -> [f] and
#: [t f f] putting the index into [tabwrite] before the value.
FILL_LOOP = make(
    ["loadbang", "msg|512", "until", "f", "+ 1", "t f f",
     "expr sin($f1 * 2 * 3.14159265 / 512)", "tabwrite w", "table w 512", *PLAY_512],
    "0.0>1.0 1.0>2.0 2.0>3.0 3.0>4.0 4.0>3.1 3.0>5.0 5.1>7.1 5.0>6.0 6.0>7.0 " + _play(9),
)


def _without_playback(patch, keep: int):
    """The fill half only, so a read-back fits in the 15-box limit."""
    objects = [o.model_dump() for o in patch.objects[:keep]]
    ids = {o["id"] for o in objects}
    wires = [c.model_dump() for c in patch.connections if c.srcId in ids and c.dstId in ids]
    return objects, wires


def _read_back(patch, keep: int, index: int) -> float:
    """Run the skill's check: [loadbang] -> [delay 10] -> [index( -> [tabread w] -> [print]."""
    objects, wires = _without_playback(patch, keep)
    n = len(objects)
    for i, text in enumerate(["loadbang", "delay 10", str(index), "tabread w", "print rb"]):
        objects.append({"id": str(n + i), "type": "msg" if i == 2 else "obj", "text": text,
                        "inlets": 2, "outlets": 2})
    wires += [{"srcId": str(n + i), "srcOutlet": 0, "dstId": str(n + i + 1), "dstInlet": 0}
              for i in range(4)]
    from pd_schema import PdPatch
    result = run_patch(runnable_file(PdPatch(objects=objects, connections=wires), []), 0.5)
    line = next(line for line in result.console.splitlines() if line.startswith("rb:"))
    return float(line.split(":", 1)[1])


class TestPromptRule:
    RULE = "check the values before you change the wiring"

    def test_the_execution_prompt_asks_for_a_read_back_before_rewiring(self):
        prompt = " ".join(agent_system_prompt(execution=True).split())
        assert self.RULE in prompt
        assert "[tabread]" in prompt and "[print]" in prompt

    def test_the_rule_is_not_given_when_patches_cannot_run(self):
        assert self.RULE not in " ".join(agent_system_prompt(execution=False).split())


class TestSkill:
    @pytest.fixture
    def skill(self):
        from pathlib import Path
        registry = SkillRegistry(Path(__file__).parent.parent / "skills", activation=False)
        return registry.get("wavetables")

    def test_the_skill_is_installed_with_a_golden_set(self, skill):
        assert skill is not None
        assert "tabosc4~" in skill.description and "fill" in skill.description

    @pytest.mark.parametrize("fact", [
        "sinesum", "guard points", "[t f f]", "[until]` → `[f]", "[tabread wave]",
        "the creation argument is the table name",
    ])
    def test_the_skill_states_each_fix_for_the_recorded_failures(self, skill, fact):
        assert fact in skill.instructions


@needs_sandbox
class TestSandbox:
    @pytest.mark.parametrize("patch", [RECORDED, UNROUTED_SINESUM], ids=["recorded", "unrouted"])
    def test_the_failing_shapes_run_cleanly_and_are_silent(self, patch):
        score = score_patch(SPEC, patch)
        assert score["clean"] and score["problems"] == ["silent"], score

    @pytest.mark.parametrize(
        "patch", [SINESUM_TABOSC, SINESUM_TABREAD, FILL_LOOP],
        ids=["sinesum-tabosc4", "sinesum-tabread4", "fill-loop"])
    def test_every_route_the_skill_prescribes_meets_the_spec(self, patch):
        score = score_patch(SPEC, patch)
        assert score["passed"], score

    def test_the_read_back_tells_an_empty_table_from_a_filled_one(self):
        assert _read_back(RECORDED, 9, 128) == 0
        assert _read_back(FILL_LOOP, 9, 128) == pytest.approx(1, abs=1e-3)
        # sinesum keeps one guard point before the cycle, so its quarter cycle is 129.
        assert _read_back(SINESUM_TABOSC, 3, 129) == pytest.approx(1, abs=1e-3)
