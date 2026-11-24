"""Tests for repairing pd-patch counts against the retrieved excerpt (TASK-5.24).

The agent writes a number in every pd-patch ``inlets``/``outlets`` field, and for
``[snapshot~]`` it kept writing two even though the IEM reference lists one inlet
row. These tests pin the parser, the safety rules (variable arity, unknown
objects, out-of-range connections) and the JSON-only rewrite. No API calls.
"""

from __future__ import annotations

import json

import pytest

from agent import patch_counts


def _chunk(name, text, *, heading=None, url=None):
    return {
        "id": f"parent-{name}",
        "object_name": name,
        "heading_path": heading or name,
        "text": text,
        "url": url or f"https://pd.iem.sh/objects/{name}/",
    }


SNAPSHOT_CHUNK = _chunk(
    "snapshot~",
    "Object: snapshot~\n\n"
    "Description: convert a signal to a number on demand\n\n"
    "Inlets:\n"
    "1st | bang - convert a signal to a float. | set <float> - set a float value "
    "for the next DSP block.\n\n"
    "Outlets:\n"
    "1st | float - the converted signal at every bang.\n\n"
    "See also: sig~\n",
)
OSC_CHUNK = _chunk(
    "osc~",
    "Object: osc~\n\nInlets:\n"
    "1st | signal - frequency value in Hz.\n"
    "2nd | float - phase cycle reset (from 0 to 1).\n\n"
    "Outlets:\n1st | signal - cosine waveform (in the range of -1 to 1).\n",
)
PACK_CHUNK = _chunk(
    "pack",
    "Object: pack\n\nInlets:\n"
    "1st | anything - ... | bang - output the packed list.\n"
    "n: (number depends on number of arguments)\n\n"
    "Outlets:\n1st | list - the packed list.\n",
)
DAC_CHUNK = _chunk(
    "dac~",
    "Object: dac~\n\nInlets:\n"
    "1st | set <list> - resets the channel(s).\n"
    "n | signal - signal output to sound card.\n",
)
MANUAL_CHUNK = {
    "id": "msp-parent-1",
    "object_name": None,
    "heading_path": "Chapter 2 > 2.5. Converting audio and control signals",
    "text": "snapshot~ takes a signal and converts it to control on a bang.",
    "url": "http://msp.ucsd.edu/Pd_documentation/x2.htm",
}


def _patch(*objects, connections=None) -> str:
    data = {"objects": list(objects), "connections": connections or []}
    return "```pd-patch\n" + json.dumps(data) + "\n```"


def _obj(id, text, inlets, outlets, type="obj"):
    return {"id": id, "type": type, "text": text, "inlets": inlets, "outlets": outlets}


class TestDocumentedCounts:
    def test_parses_fixed_inlet_and_outlet_rows(self):
        counts = patch_counts.documented_counts(SNAPSHOT_CHUNK["text"])
        assert counts.inlets == 1
        assert counts.outlets == 1

    def test_parses_two_inlets(self):
        counts = patch_counts.documented_counts(OSC_CHUNK["text"])
        assert counts.inlets == 2
        assert counts.outlets == 1

    def test_variable_arity_is_unknown(self):
        # `n:` in pack's inlet table means the count depends on the arguments.
        assert patch_counts.documented_counts(PACK_CHUNK["text"]).inlets is None
        # dac~ has an `n |` row and no Outlets section at all.
        assert patch_counts.documented_counts(DAC_CHUNK["text"]).inlets is None
        assert patch_counts.documented_counts(DAC_CHUNK["text"]).outlets is None

    def test_a_section_that_only_names_an_object_is_not_a_count(self):
        counts = patch_counts.documented_counts(MANUAL_CHUNK["text"])
        assert counts.inlets is None
        assert counts.outlets is None

    def test_conflicting_sources_are_not_used(self):
        other = _chunk("snapshot~", "Object: snapshot~\n\nInlets:\n1st | ...\n2nd | ...\n")
        index = patch_counts.documented_counts_by_object([SNAPSHOT_CHUNK, other])
        assert "snapshot~" not in index

    def test_manual_chunks_without_an_object_name_are_ignored(self):
        index = patch_counts.documented_counts_by_object([MANUAL_CHUNK])
        assert index == {}


class TestRepair:
    def test_repairs_the_recorded_snapshot_count(self):
        text = "Use [snapshot~]:\n" + _patch(
            _obj("0", "osc~ 440", 2, 1),
            _obj("1", "snapshot~", 2, 1),
            connections=[{"srcId": "0", "srcOutlet": 0, "dstId": "1", "dstInlet": 0}],
        )
        repaired, repairs = patch_counts.repair_patch_counts(
            text, [SNAPSHOT_CHUNK, OSC_CHUNK]
        )
        assert repairs == 1
        patch = json.loads(
            repaired.split("```pd-patch")[1].split("```")[0].strip()
        )
        snapshot = next(o for o in patch["objects"] if o["text"] == "snapshot~")
        assert snapshot["inlets"] == 1
        assert snapshot["outlets"] == 1
        # The prose around the block is untouched.
        assert repaired.startswith("Use [snapshot~]:\n")

    def test_leaves_correct_counts_alone(self):
        text = _patch(_obj("0", "snapshot~", 1, 1))
        repaired, repairs = patch_counts.repair_patch_counts(text, [SNAPSHOT_CHUNK])
        assert repairs == 0
        assert repaired == text

    def test_variable_arity_objects_are_not_touched(self):
        text = _patch(_obj("0", "pack f f", 2, 1), _obj("1", "dac~", 2, 0))
        repaired, repairs = patch_counts.repair_patch_counts(
            text, [PACK_CHUNK, DAC_CHUNK]
        )
        assert repairs == 0
        assert repaired == text

    def test_unknown_objects_are_not_touched(self):
        text = _patch(_obj("0", "my-external~", 3, 2))
        repaired, repairs = patch_counts.repair_patch_counts(text, [SNAPSHOT_CHUNK])
        assert repairs == 0
        assert repaired == text

    def test_repairs_both_counts_when_both_are_documented(self):
        text = _patch(_obj("0", "osc~ 440", 1, 0))
        repaired, repairs = patch_counts.repair_patch_counts(text, [OSC_CHUNK])
        assert repairs == 1
        patch = json.loads(repaired.split("```pd-patch")[1].split("```")[0].strip())
        assert patch["objects"][0]["inlets"] == 2
        assert patch["objects"][0]["outlets"] == 1

    def test_a_connection_to_the_removed_inlet_blocks_the_repair(self):
        # The diagram wires the bang to a second inlet; shrinking the count would
        # leave that connection pointing at a nub that no longer exists.
        text = _patch(
            _obj("0", "snapshot~", 2, 1),
            connections=[
                {"srcId": "1", "srcOutlet": 0, "dstId": "0", "dstInlet": 1},
            ],
        )
        repaired, repairs = patch_counts.repair_patch_counts(text, [SNAPSHOT_CHUNK])
        assert repairs == 0
        assert repaired == text

    def test_a_connection_within_the_repaired_count_still_repairs(self):
        text = _patch(
            _obj("0", "snapshot~", 2, 1),
            connections=[
                {"srcId": "1", "srcOutlet": 0, "dstId": "0", "dstInlet": 0},
            ],
        )
        repaired, repairs = patch_counts.repair_patch_counts(text, [SNAPSHOT_CHUNK])
        assert repairs == 1
        assert '"inlets": 1' in repaired

    def test_malformed_json_is_left_for_the_validator(self):
        text = "```pd-patch\n{not json}\n```"
        repaired, repairs = patch_counts.repair_patch_counts(text, [SNAPSHOT_CHUNK])
        assert repairs == 0
        assert repaired == text

    def test_non_object_boxes_are_not_treated_as_objects(self):
        # A message box happens to contain the object's name in its text.
        text = _patch(_obj("0", "snapshot~", 2, 1, type="msg"))
        repaired, repairs = patch_counts.repair_patch_counts(text, [SNAPSHOT_CHUNK])
        assert repairs == 0
        assert repaired == text

    def test_multiple_blocks_are_each_repaired(self):
        first = _patch(_obj("0", "snapshot~", 2, 1))
        second = _patch(_obj("0", "osc~ 440", 1, 1))
        repaired, repairs = patch_counts.repair_patch_counts(
            first + "\n\n" + second, [SNAPSHOT_CHUNK, OSC_CHUNK]
        )
        assert repairs == 2

    def test_no_retrieved_excerpts_means_no_rewrite(self):
        text = _patch(_obj("0", "snapshot~", 2, 1))
        assert patch_counts.repair_patch_counts(text, []) == (text, 0)

    @pytest.mark.parametrize(
        "text,chunks",
        [
            (_patch(_obj("0", "snapshot~", 2, 1)), [SNAPSHOT_CHUNK]),
            (_patch(_obj("0", "osc~ 440", 1, 0)), [OSC_CHUNK]),
        ],
    )
    def test_repaired_blocks_are_still_valid_patches(self, text, chunks):
        from agent.patches import validate_patch

        repaired, repairs = patch_counts.repair_patch_counts(text, chunks)
        assert repairs == 1
        raw = repaired.split("```pd-patch")[1].split("```")[0].strip()
        assert validate_patch(raw) is not None
