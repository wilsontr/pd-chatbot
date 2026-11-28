"""The README quotes the published multi-trial baseline exactly (TASK-5.38).

docs/EVAL_BASELINE.md is rewritten only by a manual run with 3+ trials. When
that happens these tests fail until the README's figures are updated to match,
so the README cannot drift from the report it links to. docs/EVAL.md (the
routine 1-trial sample) is checked only for stating its sample.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
BASELINE = (ROOT / "docs" / "EVAL_BASELINE.md").read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    end = text.find("\n#", start + len(heading))
    return text[start: end if end != -1 else None]


def _rows(section: str) -> dict[str, list[str]]:
    rows = {}
    for line in section.splitlines():
        if line.startswith("|") and not line.startswith("|-"):
            cells = [c.strip().strip("*") for c in line.strip("|").split("|")]
            rows[cells[0]] = cells[1:]
    return rows


@pytest.fixture(scope="module")
def arms():
    rows = _rows(_section(BASELINE, "### Pipeline vs agent"))
    pipeline = next(v for k, v in rows.items() if k.endswith("(pipeline)"))
    agent = next(v for k, v in rows.items() if k.endswith("(agent)"))
    return pipeline, agent


@pytest.fixture(scope="module")
def retrieval():
    rows = _rows(_section(BASELINE, "## Retrieval"))
    return rows["Hybrid RRF"], rows["Agent tool calls"]


def _mean(cell: str) -> str:
    return cell.split("±")[0].strip()


def test_the_baseline_is_multi_trial():
    assert "**This report:** generation on 30 entries x 3 trials per arm" in BASELINE
    assert "**30 entries x 3 trials per arm.**" in BASELINE


def test_the_headline_quality_line_matches(arms):
    pipeline, agent = arms
    expected = (
        f"faithfulness **{_mean(agent[0])}** vs {_mean(pipeline[0])}, "
        f"relevance {_mean(agent[1])} vs {_mean(pipeline[1])}, "
        f"citation {_mean(agent[2])} vs {_mean(pipeline[2])}"
    )
    assert expected in README


def test_the_measured_table_matches(arms):
    pipeline, agent = arms
    for label, row in (("Pipeline", pipeline), ("Agent", agent)):
        # Arm table: Faithfulness | Relevance | Citation | n | Cost/query | p50 | p95
        p50, p95 = (row[5].replace("s", " s"), row[6].replace("s", " s"))
        expected = f"| {label} | {row[0]} | {row[1]} | {row[2]} | {row[4]} | {p50} | {p95} |"
        assert expected in README, expected


def test_the_retrieval_line_matches(retrieval):
    hybrid, agent = retrieval

    def pct(cell: str) -> str:
        return f"{float(cell.rstrip('%')):.1f}%"

    expected = (
        f"recall@5 **{pct(agent[1])}** vs {pct(hybrid[1])}, "
        f"MRR {agent[3]} vs {hybrid[3]}"
    )
    assert expected in README


def test_the_readme_cites_the_baseline_not_the_routine_report():
    intro = README[: README.index("## Sources")]
    assert "[EVAL_BASELINE.md](docs/EVAL_BASELINE.md)" in intro
    measured = README[README.index("**Measured** (2026-11-24"):].split("\n\n")[0]
    assert "(docs/EVAL_BASELINE.md)" in measured and "(docs/EVAL.md)" not in measured


def test_the_routine_report_states_its_sample():
    routine = (ROOT / "docs" / "EVAL.md").read_text(encoding="utf-8")
    if "**This report:**" not in routine:
        pytest.skip("EVAL.md predates TASK-5.38; the next push to main regenerates it")
    assert re.search(r"\*\*This report:\*\* generation on \d+ entries x \d+ trials? per arm",
                     routine)
