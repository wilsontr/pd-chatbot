"""Tests for the patch eval: every spec accepts a correct patch and rejects a wrong one."""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from evaluation.patch_eval import load_tasks, score_patch, summarize, to_markdown
from pd_schema import PdPatch

TASKS = {t["id"]: t["spec"] for t in load_tasks()}


def make(boxes, wires):
    """boxes: 'type|text' or 'text' for an obj; wires: 'a.out>b.in' by position."""
    objects = []
    for i, box in enumerate(boxes):
        kind, _, text = box.partition("|") if "|" in box else ("obj", "", box)
        objects.append({"id": str(i), "type": kind, "text": text or kind, "inlets": 2, "outlets": 2})
    connections = []
    for wire in wires.split():
        src, dst = wire.split(">")
        a, ao = src.split(".")
        b, bi = dst.split(".")
        connections.append(
            {"srcId": a, "srcOutlet": int(ao), "dstId": b, "dstInlet": int(bi)})
    return PdPatch(objects=objects, connections=connections)


GOOD = {
    "p_01": make(["osc~ 440", "*~ 0.5", "dac~"], "0.0>1.0 1.0>2.0 1.0>2.1"),
    "p_02": make(["osc~ 300", "osc~ 450", "*~ 0.4", "dac~"], "0.0>2.0 1.0>2.0 2.0>3.0"),
    "p_03": make(["osc~ 100", "*~ 100", "+~ 400", "osc~", "*~ 0.5", "dac~"],
                 "0.0>1.0 1.0>2.0 2.0>3.0 3.0>4.0 4.0>5.0"),
    "p_04": make(["osc~ 500", "osc~ 80", "*~", "dac~"], "0.0>2.0 1.0>2.1 2.0>3.0"),
    "p_05": make(["noise~", "lop~ 500", "dac~"], "0.0>1.0 1.0>2.0"),
    "p_06": make(["loadbang", r"msg|1, 0 1000", "line~", "osc~ 330", "*~", "dac~"],
                 "0.0>1.0 1.0>2.0 2.0>4.1 3.0>4.0 4.0>5.0"),
    "p_07": make(["phasor~ 110", "-~ 0.5", "dac~"], "0.0>1.0 1.0>2.0"),
    "p_08": make(["loadbang", "msg|200, 800 2000", "line~", "osc~", "dac~"],
                 "0.0>1.0 1.0>2.0 2.0>3.0 3.0>4.0"),
    "p_09": make(["floatatom|", "mtof", "osc~", "dac~"], "0.0>1.0 1.0>2.0 2.0>3.0"),
    "p_10": make(["loadbang", "metro 200", "f", "+ 1", "print"],
                 "0.0>1.0 1.0>2.0 2.0>3.0 3.0>2.1 2.0>4.0"),
    "p_11": make(["loadbang", "msg|1, 0 100", "line~", "osc~ 500", "*~", "dac~",
                  "delwrite~ d 2000", "delread~ d 1000"],
                 "0.0>1.0 1.0>2.0 2.0>4.1 3.0>4.0 4.0>5.0 4.0>6.0 7.0>5.0"),
    "p_12": make(["osc~ 440", "osc~ 660", "dac~"], "0.0>2.0 1.0>2.1"),
    "p_13": make(["noise~", "bp~ 1000 20", "*~ 5", "dac~"], "0.0>1.0 1.0>2.0 2.0>3.0"),
    "p_14": make(["loadbang", "msg|; w sinesum 512 1", "table w 515", "tabosc4~ w",
                  "sig~ 220", "dac~"], "0.0>1.0 4.0>3.0 3.0>5.0"),
    "p_15": make(["tgl|", "metro 250", "msg|1, 0 50", "line~", "osc~ 1000", "*~", "dac~"],
                 "0.0>1.0 1.0>2.0 2.0>3.0 3.0>5.1 4.0>5.0 5.0>6.0"),
    "p_16": make(["osc~ 5", "*~ 0.4", "+~ 0.5", "osc~ 300", "*~", "dac~"],
                 "0.0>1.0 1.0>2.0 2.0>4.1 3.0>4.0 4.0>5.0"),
}
#: A near miss for each task: plausible, and not what was asked.
BAD = {
    "p_01": make(["osc~ 440", "dac~"], "0.0>1.0 0.0>1.1"),
    "p_02": make(["osc~ 300", "osc~ 450", "dac~"], "0.0>2.0 1.0>2.0"),
    "p_03": make(["osc~ 400", "osc~ 100", "*~ 0.5", "dac~"], "0.0>2.0 1.0>2.0 2.0>3.0"),
    "p_04": make(["osc~ 500", "osc~ 80", "+~", "*~ 0.4", "dac~"],
                 "0.0>2.0 1.0>2.1 2.0>3.0 3.0>4.0"),
    "p_05": make(["noise~", "hip~ 500", "dac~"], "0.0>1.0 1.0>2.0"),
    "p_06": make(["osc~ 330", "dac~"], "0.0>1.0"),
    "p_07": make(["phasor~ 110", "dac~"], "0.0>1.0"),
    "p_08": make(["loadbang", "msg|800, 200 2000", "line~", "osc~", "dac~"],
                 "0.0>1.0 1.0>2.0 2.0>3.0 3.0>4.0"),
    "p_09": make(["floatatom|", "osc~", "dac~"], "0.0>1.0 1.0>2.0"),
    "p_10": make(["loadbang", "metro 200", "f", "+ 1", "print"],
                 "0.0>1.0 1.0>2.0 2.0>3.0 3.0>2.1 3.0>4.0"),
    "p_11": make(["loadbang", "msg|1, 0 100", "line~", "osc~ 500", "*~", "dac~"],
                 "0.0>1.0 1.0>2.0 2.0>4.1 3.0>4.0 4.0>5.0"),
    "p_12": make(["osc~ 440", "osc~ 660", "dac~"], "0.0>2.0 0.0>2.1 1.0>2.0 1.0>2.1"),
    "p_13": make(["noise~", "bp~ 3000 20", "*~ 5", "dac~"], "0.0>1.0 1.0>2.0 2.0>3.0"),
    "p_14": make(["table w 515", "tabosc4~ w", "sig~ 220", "dac~"], "2.0>1.0 1.0>3.0"),
    "p_15": make(["tgl|", "metro 250", "osc~ 1000", "dac~"], "0.0>1.0"),
    "p_16": make(["osc~ 300", "*~ 0.5", "dac~"], "0.0>1.0 1.0>2.0"),
}


def _image():
    return shutil.which("docker") and subprocess.run(
        ["docker", "image", "inspect", os.environ.get("PD_SANDBOX_IMAGE", "pd-sandbox:latest")],
        capture_output=True).returncode == 0


needs_sandbox = pytest.mark.skipif(
    not os.environ.get("PD_SANDBOX_REQUIRED") and not _image(), reason="pd-sandbox image not built")


def test_every_task_has_both_controls():
    assert set(GOOD) == set(BAD) == set(TASKS)


@needs_sandbox
@pytest.mark.parametrize("task", sorted(TASKS))
def test_correct_patch_passes(task):
    score = score_patch(TASKS[task], GOOD[task])
    assert score["passed"], score


@needs_sandbox
@pytest.mark.parametrize("task", sorted(TASKS))
def test_near_miss_fails_the_spec_but_runs(task):
    score = score_patch(TASKS[task], BAD[task])
    assert not score["meets_spec"], score


@needs_sandbox
def test_pd_error_is_not_a_pass():
    patch = make(["osc~ 440", "*~ 0.5", "dac~", "nosuch~"], "0.0>1.0 1.0>2.0 1.0>2.1")
    score = score_patch(TASKS["p_01"], patch)
    assert score["meets_spec"] and not score["clean"] and not score["passed"]


def test_summary_separates_outages_from_failures():
    row = {"id": "p_01", "trial": 1, "wrote_patch": True, "clean": True, "passed": True,
           "latency_s": 1.0, "cost_usd": 0.01, "tool_calls": 2, "runs": 1}
    rows = [{**row, "arm": "with"}, {**row, "arm": "with", "passed": False},
            {"id": "p_01", "arm": "with", "trial": 1, "error": "x"}]
    summary = summarize(rows)
    assert summary["with"]["n"] == 2 and summary["with"]["passed"] == 0.5
    assert summary["with"]["infrastructure_errors"] == 1 and "without" not in summary
    assert "50%" in to_markdown(summary, rows)
