"""pd-patch block handling: validate, canonicalise, strip for history.

The model emits patch diagrams as fenced ```pd-patch JSON blocks inside its prose.
These helpers extract them, validate them against the Pydantic schema, and either
canonicalise the JSON (so the frontend gets a stable shape) or replace a
malformed block with a placeholder. Validation failure is reported through a
custom metric and, on the non-streaming path, triggers one corrective retry.
"""

from __future__ import annotations

import json
import logging
import re

import newrelic.agent

from pd_schema import PdPatch

logger = logging.getLogger(__name__)

_PATCH_FENCE_RE = re.compile(r'```pd-patch\b\n?(.*?)```', re.DOTALL)

RETRY_PROMPT = """Your previous response contained a pd-patch code block with invalid JSON.

Please fix the pd-patch JSON block to match this exact schema:
```json
{
  "objects": [{"id": "str", "type": "obj|msg|floatatom|comment", "text": "str", "inlets": int, "outlets": int}],
  "connections": [{"srcId": "str", "srcOutlet": int, "dstId": "str", "dstInlet": int}]
}
```

Rules:
- objects array: 1-15 items, each with id, type (one of obj/msg/floatatom/comment), text, inlets (>=0), outlets (>=0)
- connections array: unlimited, each with srcId, srcOutlet (>=0), dstId, dstInlet (>=0)
- All id references in connections must match object ids

Do not change the rest of your answer. Only fix the pd-patch JSON block."""


def strip_patch_blocks(text: str) -> str:
    """Remove pd-patch JSON blocks before storing in history.
    The LLM doesn't need to re-read its own patch JSON on follow-up turns,
    and leaving it in would inflate context tokens and risk length validation errors."""
    # Strip complete blocks first (opening + closing fence)
    text = re.sub(r'```pd-patch\b.*?```', '[patch diagram]', text, flags=re.DOTALL)
    # Strip truncated blocks — no closing fence, runs to end of string
    text = re.sub(r'```pd-patch\b.*', '[patch diagram]', text, flags=re.DOTALL)
    return text.strip()


def extract_pd_patches(text: str) -> list[tuple[str, str]]:
    """Return list of (raw_json, full_fenced_block) for each pd-patch block."""
    return [(m.group(1).strip(), m.group(0)) for m in _PATCH_FENCE_RE.finditer(text)]


def validate_patch(raw_json: str) -> PdPatch | None:
    """Parse and validate a pd-patch JSON block. Returns PdPatch or None."""
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        logger.warning("pd-patch JSON parse error: %s", e)
        return None
    try:
        return PdPatch.model_validate(data)
    except Exception as e:
        logger.warning("pd-patch schema validation failed: %s", e)
        return None


def validate_and_canonicalize_blocks(text: str) -> tuple[str, bool]:
    """Extract pd-patch blocks, validate each, replace with canonical JSON or placeholder.

    Returns (processed_text, had_failure).
    Tracks parse failures via New Relic custom metric.
    """
    had_failure = False
    result = text
    for raw_json, full_block in extract_pd_patches(text):
        patch = validate_patch(raw_json)
        if patch is not None:
            canonical = json.dumps(patch.model_dump(), separators=(",", ":"), ensure_ascii=False)
            replacement = f"```pd-patch\n{canonical}\n```"
        else:
            had_failure = True
            newrelic.agent.record_custom_metric("Custom/PdPatch/ParseFailure", 1)
            replacement = "[patch diagram]"
        result = result.replace(full_block, replacement, 1)
    return result, had_failure


def canonical_patches(text: str) -> list[dict]:
    """Validated patches in ``text``, ready to publish as artifacts."""
    patches = []
    for raw_json, _ in extract_pd_patches(text):
        patch = validate_patch(raw_json)
        if patch is not None:
            patches.append(patch.model_dump())
    return patches
