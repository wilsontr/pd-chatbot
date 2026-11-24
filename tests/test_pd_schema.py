"""Tests for pd-patch structured validation."""

import json
import re
from pathlib import Path

import pytest
from pd_schema import PD_OBJECT_TYPES, PdPatch, PdObject, PdConnection
from agent.prompt import AGENT_SYSTEM_PROMPT, SYSTEM_PROMPT
from agent.patches import (
    RETRY_PROMPT,
    extract_pd_patches,
    validate_and_canonicalize_blocks,
    validate_patch,
)

_VALID_PATCH_JSON = json.dumps(
    {
        "objects": [
            {"id": "0", "type": "obj", "text": "osc~ 440", "inlets": 2, "outlets": 1},
            {"id": "1", "type": "obj", "text": "dac~", "inlets": 2, "outlets": 0},
        ],
        "connections": [
            {"srcId": "0", "srcOutlet": 0, "dstId": "1", "dstInlet": 0},
        ],
    }
)


class TestPdPatchSchema:
    def test_valid_minimal_patch(self):
        patch = PdPatch.model_validate(
            {"objects": [{"id": "0", "type": "obj", "text": "osc~ 440", "inlets": 2, "outlets": 1}],
             "connections": []}
        )
        assert len(patch.objects) == 1

    def test_invalid_type_rejected(self):
        with pytest.raises(Exception):
            PdPatch.model_validate(
                {"objects": [{"id": "0", "type": "not_a_real_type", "text": "x", "inlets": 1, "outlets": 1}],
                 "connections": []}
            )

    def test_negative_inlets_rejected(self):
        with pytest.raises(Exception):
            PdPatch.model_validate(
                {"objects": [{"id": "0", "type": "obj", "text": "x", "inlets": -1, "outlets": 1}],
                 "connections": []}
            )

    def test_too_many_objects_rejected(self):
        objects = [{"id": str(i), "type": "obj", "text": f"obj{i}", "inlets": 1, "outlets": 1}
                   for i in range(16)]
        with pytest.raises(Exception):
            PdPatch.model_validate({"objects": objects, "connections": []})

    def test_empty_objects_rejected(self):
        with pytest.raises(Exception):
            PdPatch.model_validate({"objects": [], "connections": []})


_TYPES_TS = Path(__file__).resolve().parent.parent / "frontend" / "src" / "types.ts"

# Spelled out rather than read from the schema, so dropping a type fails a test.
_EXPECTED_TYPES = [
    "obj", "msg", "floatatom", "comment", "table", "array", "bng", "tgl",
    "hsl", "vsl", "hradio", "vradio", "nbx", "vu", "cnv",
]


class TestBoxTypes:
    def test_expected_types(self):
        assert sorted(PD_OBJECT_TYPES) == sorted(_EXPECTED_TYPES)

    @pytest.mark.parametrize("box_type", _EXPECTED_TYPES)
    def test_type_survives_block_validation(self, box_type):
        patch = {
            "objects": [
                {"id": "0", "type": box_type, "text": f"{box_type} x", "inlets": 1, "outlets": 1}
            ],
            "connections": [],
        }
        text, failed = validate_and_canonicalize_blocks(
            f"```pd-patch\n{json.dumps(patch)}\n```"
        )
        assert not failed
        assert json.loads(extract_pd_patches(text)[0][0]) == patch

    @pytest.mark.parametrize("box_type", _EXPECTED_TYPES)
    def test_retry_prompt_lists_type(self, box_type):
        assert box_type in re.search(r'"type": "([^"]+)"', RETRY_PROMPT).group(1).split("|")

    @pytest.mark.parametrize("prompt", [SYSTEM_PROMPT, AGENT_SYSTEM_PROMPT])
    @pytest.mark.parametrize("box_type", _EXPECTED_TYPES)
    def test_answer_prompt_lists_type(self, prompt, box_type):
        types = prompt.split("type is one of:")[1].split("inlets and outlets must")[0]
        # A definition is followed by "(", or by "and <type> (" for the paired widgets.
        assert re.search(rf"\b{box_type} (?:and \w+ )?\(", types)

    def test_frontend_types_match(self):
        source = _TYPES_TS.read_text()
        # Drop comments: they contain quoted examples.
        source = re.sub(r"//.*", "", source)
        union = re.search(r"export type PdObjectType =(.*?)(?=\nexport )", source, re.DOTALL)
        assert sorted(re.findall(r"'([^']+)'", union.group(1))) == sorted(PD_OBJECT_TYPES)

    @pytest.mark.parametrize("model", [PdObject, PdConnection, PdPatch])
    def test_frontend_fields_match(self, model):
        source = re.sub(r"//.*", "", _TYPES_TS.read_text())
        body = re.search(
            rf"export interface {model.__name__} \{{(.*?)\}}", source, re.DOTALL
        ).group(1)
        assert set(re.findall(r"^\s*(\w+):", body, re.MULTILINE)) == set(model.model_fields)


class TestValidatePatch:
    def test_valid_patch(self):
        result = validate_patch(_VALID_PATCH_JSON)
        assert result is not None
        assert len(result.objects) == 2

    def test_malformed_json_returns_none(self):
        assert validate_patch("{not json}") is None

    def test_wrong_schema_returns_none(self):
        assert validate_patch('{"wrong": "schema"}') is None


class TestExtractPdPatches:
    def test_single_block(self):
        text = f"Here is a patch:\n\n```pd-patch\n{_VALID_PATCH_JSON}\n```\n\nEnd."
        patches = extract_pd_patches(text)
        assert len(patches) == 1
        raw, block = patches[0]
        assert "osc~" in raw
        assert block.startswith("```pd-patch")

    def test_multiple_blocks(self):
        text = f"First:\n```pd-patch\n{_VALID_PATCH_JSON}\n```\nSecond:\n```pd-patch\n{_VALID_PATCH_JSON}\n```"
        patches = extract_pd_patches(text)
        assert len(patches) == 2

    def test_no_blocks(self):
        assert extract_pd_patches("No patch here.") == []

    def test_does_not_match_other_languages(self):
        text = "```pd-patch\n[1,2,3]\n```\n```python\nprint(1)\n```"
        patches = extract_pd_patches(text)
        assert len(patches) == 1


class TestValidateAndCanonicalizeBlocks:
    def test_valid_block_kept(self):
        text = f"```pd-patch\n{_VALID_PATCH_JSON}\n```"
        result, failed = validate_and_canonicalize_blocks(text)
        assert not failed
        assert "```pd-patch" in result
        assert "osc~" in result

    def test_invalid_block_replaced(self):
        text = "```pd-patch\n{not json}\n```"
        result, failed = validate_and_canonicalize_blocks(text)
        assert failed
        assert "[patch diagram]" in result
        assert "{not json}" not in result

    def test_mixed_blocks(self):
        text = f"Good:\n```pd-patch\n{_VALID_PATCH_JSON}\n```\nBad:\n```pd-patch\n{{broken}}\n```"
        result, failed = validate_and_canonicalize_blocks(text)
        assert failed
        assert "osc~" in result
        assert "[patch diagram]" in result
        assert "{broken}" not in result

    def test_no_patch_blocks_noop(self):
        text = "Just prose, no patches."
        result, failed = validate_and_canonicalize_blocks(text)
        assert not failed
        assert result == text

    def test_canonicalizes_valid_json(self):
        # Input with extra whitespace gets canonicalized
        text = '```pd-patch\n{"objects":  [{"id":"0","type":"obj","text":"x","inlets":1,"outlets":1}], "connections":[]}\n```'
        result, failed = validate_and_canonicalize_blocks(text)
        assert not failed
        # Should be compact now
        assert '"id":"0"' in result
        assert '"objects":[' in result
