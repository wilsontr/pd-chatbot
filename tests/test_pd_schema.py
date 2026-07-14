"""Tests for pd-patch structured validation."""

import json

import pytest
from pd_schema import PdPatch, PdObject, PdConnection
from rag import _extract_pd_patches, _validate_patch, _validate_and_canonicalize_blocks

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


class TestValidatePatch:
    def test_valid_patch(self):
        result = _validate_patch(_VALID_PATCH_JSON)
        assert result is not None
        assert len(result.objects) == 2

    def test_malformed_json_returns_none(self):
        assert _validate_patch("{not json}") is None

    def test_wrong_schema_returns_none(self):
        assert _validate_patch('{"wrong": "schema"}') is None


class TestExtractPdPatches:
    def test_single_block(self):
        text = f"Here is a patch:\n\n```pd-patch\n{_VALID_PATCH_JSON}\n```\n\nEnd."
        patches = _extract_pd_patches(text)
        assert len(patches) == 1
        raw, block = patches[0]
        assert "osc~" in raw
        assert block.startswith("```pd-patch")

    def test_multiple_blocks(self):
        text = f"First:\n```pd-patch\n{_VALID_PATCH_JSON}\n```\nSecond:\n```pd-patch\n{_VALID_PATCH_JSON}\n```"
        patches = _extract_pd_patches(text)
        assert len(patches) == 2

    def test_no_blocks(self):
        assert _extract_pd_patches("No patch here.") == []

    def test_does_not_match_other_languages(self):
        text = "```pd-patch\n[1,2,3]\n```\n```python\nprint(1)\n```"
        patches = _extract_pd_patches(text)
        assert len(patches) == 1


class TestValidateAndCanonicalizeBlocks:
    def test_valid_block_kept(self):
        text = f"```pd-patch\n{_VALID_PATCH_JSON}\n```"
        result, failed = _validate_and_canonicalize_blocks(text)
        assert not failed
        assert "```pd-patch" in result
        assert "osc~" in result

    def test_invalid_block_replaced(self):
        text = "```pd-patch\n{not json}\n```"
        result, failed = _validate_and_canonicalize_blocks(text)
        assert failed
        assert "[patch diagram]" in result
        assert "{not json}" not in result

    def test_mixed_blocks(self):
        text = f"Good:\n```pd-patch\n{_VALID_PATCH_JSON}\n```\nBad:\n```pd-patch\n{{broken}}\n```"
        result, failed = _validate_and_canonicalize_blocks(text)
        assert failed
        assert "osc~" in result
        assert "[patch diagram]" in result
        assert "{broken}" not in result

    def test_no_patch_blocks_noop(self):
        text = "Just prose, no patches."
        result, failed = _validate_and_canonicalize_blocks(text)
        assert not failed
        assert result == text

    def test_canonicalizes_valid_json(self):
        # Input with extra whitespace gets canonicalized
        text = '```pd-patch\n{"objects":  [{"id":"0","type":"obj","text":"x","inlets":1,"outlets":1}], "connections":[]}\n```'
        result, failed = _validate_and_canonicalize_blocks(text)
        assert not failed
        # Should be compact now
        assert '"id":"0"' in result
        assert '"objects":[' in result
