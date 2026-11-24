"""Keep pd-patch inlet/outlet counts consistent with the object's own excerpt.

The agent writes patch diagrams as JSON, and the schema makes it put a number in
every ``inlets``/``outlets`` field. When the retrieved reference does not state a
count — ``snapshot~`` lists a single inlet row, but the model believes (from
outside the docs) that a signal inlet is a second one — the diagram asserts a
number the answer's prose just said it could not confirm (TASK-5.24).

Prompt rules did not stop this: the number is in the JSON the schema asks for,
not in the prose. This module repairs it at the patch boundary instead. It reads
the *retrieved* object-reference excerpts (the same evidence the answer must
attribute to), and where an excerpt lists a fixed number of inlets/outlets it
rewrites a contradicting count in the patch JSON. Objects whose reference uses a
variable arity marker (``n:``/``n |``/"number depends", e.g. ``pack``,
``unpack``, ``trigger``'s outlets, ``dac~``) are left alone, as are objects the
excerpts do not cover and patches whose connections would fall outside the
repaired count.

The repair changes only the pd-patch JSON, never the prose, and it is applied on
both the serving path (``agent.chat.stream_chat``) and the eval path
(``agent.chat.answer_question``) so the measured answer is the shipped one.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import newrelic.agent

from .patches import extract_pd_patches

logger = logging.getLogger(__name__)

_SECTION_HEADERS = ("inlets:", "outlets:")
_END_HEADERS = (
    "object:",
    "description:",
    "arguments:",
    "see also:",
    "abbreviation:",
)
_FIXED_ROW_RE = re.compile(r"^\d+(?:st|nd|rd|th)\s*[:|]")
_VARIABLE_ROW_RE = re.compile(
    r"^n\b|\(number depends|\(depends on the number", re.IGNORECASE
)


@dataclass(frozen=True)
class DocumentedCounts:
    """Fixed counts parsed from one object's reference excerpt (None = unknown)."""

    inlets: int | None = None
    outlets: int | None = None


def _count_rows(rows: list[str]) -> int | None:
    """Rows like ``1st | ...`` are inlets; an ``n`` row means variable arity."""
    if not rows:
        return None
    fixed = [row for row in rows if _FIXED_ROW_RE.match(row)]
    if not fixed or any(_VARIABLE_ROW_RE.search(row) for row in rows):
        return None
    return len(fixed)


def documented_counts(text: str) -> DocumentedCounts:
    """Parse the fixed inlet/outlet counts from an IEM object-reference excerpt.

    The reference format is::

        Inlets:
        1st | bang - ... | set <float> - ...
        2nd | float - ...

        Outlets:
        1st | float - ...

    A section with an ``n`` row ("number depends on arguments") is variable and
    reports ``None``. A missing section (``dac~`` has no ``Outlets:`` line) is
    also ``None``, so nothing is enforced from its absence.
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        lowered = line.lower()
        if lowered in _SECTION_HEADERS:
            current = lowered[:-1]
            sections.setdefault(current, [])
            continue
        if current and lowered.startswith(_END_HEADERS):
            current = None
            continue
        if current and line:
            sections[current].append(line)
    return DocumentedCounts(
        inlets=_count_rows(sections.get("inlets", [])),
        outlets=_count_rows(sections.get("outlets", [])),
    )


def documented_counts_by_object(
    chunks: Iterable[Mapping[str, Any]],
) -> dict[str, DocumentedCounts]:
    """Index the retrieved excerpts by object name, skipping conflicting sources.

    A name with two corroborating counts is usable; a name whose excerpts
    disagree (or parse to nothing) is left out so the repair never guesses.
    """
    parsed: dict[str, list[DocumentedCounts]] = {}
    for chunk in chunks:
        name = chunk.get("object_name")
        text = chunk.get("text")
        if not isinstance(name, str) or not name or not isinstance(text, str):
            continue
        counts = documented_counts(text)
        if counts.inlets is None and counts.outlets is None:
            continue
        parsed.setdefault(name, []).append(counts)

    index: dict[str, DocumentedCounts] = {}
    for name, values in parsed.items():
        inlets = {value.inlets for value in values if value.inlets is not None}
        outlets = {value.outlets for value in values if value.outlets is not None}
        if len(inlets) > 1 or len(outlets) > 1:
            logger.debug("conflicting documented counts for %r; not repairing", name)
            continue
        index[name] = DocumentedCounts(
            inlets=next(iter(inlets)) if inlets else None,
            outlets=next(iter(outlets)) if outlets else None,
        )
    return index


def _object_name(text: Any) -> str:
    """First token of an object box's text (``"osc~ 440"`` -> ``"osc~"``)."""
    if not isinstance(text, str):
        return ""
    parts = text.strip().split()
    return parts[0] if parts else ""


def _connection_out_of_range(
    data: Mapping[str, Any], object_id: str, field: str, documented: int
) -> bool:
    """True when a connection would point outside the repaired count.

    Refusing the repair in that case keeps the diagram drawable: a nub the
    model wired to a second inlet cannot silently disappear. It leaves the
    unconfirmed count in place, which the eval's deterministic check can still
    see.
    """
    index_key, object_key = (
        ("dstInlet", "dstId") if field == "inlets" else ("srcOutlet", "srcId")
    )
    for connection in data.get("connections") or []:
        if not isinstance(connection, Mapping):
            continue
        if connection.get(object_key) != object_id:
            continue
        index = connection.get(index_key)
        if isinstance(index, int) and index >= documented:
            return True
    return False


def repair_patch_counts(
    text: str, chunks: Iterable[Mapping[str, Any]]
) -> tuple[str, int]:
    """Rewrite pd-patch counts that contradict the retrieved reference.

    Returns ``(text, repairs)`` where ``repairs`` is the number of blocks
    changed. Malformed JSON blocks and objects the excerpts do not cover are
    passed through untouched; ``agent.patches`` validates them separately.
    """
    index = documented_counts_by_object(chunks)
    if not index:
        return text, 0

    result = text
    repairs = 0
    for raw_json, full_block in extract_pd_patches(text):
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict) or not isinstance(data.get("objects"), list):
            continue

        changed = False
        for obj in data["objects"]:
            if not isinstance(obj, dict) or obj.get("type") != "obj":
                continue
            counts = index.get(_object_name(obj.get("text")))
            if counts is None:
                continue
            for field, documented in (
                ("inlets", counts.inlets),
                ("outlets", counts.outlets),
            ):
                current = obj.get(field)
                if documented is None or current == documented:
                    continue
                if not isinstance(current, int):
                    continue
                if _connection_out_of_range(
                    data, str(obj.get("id")), field, documented
                ):
                    continue
                obj[field] = documented
                changed = True
                newrelic.agent.record_custom_metric(
                    "Custom/PdPatch/CountRepaired", 1
                )

        if changed:
            replacement = (
                "```pd-patch\n" + json.dumps(data, indent=2) + "\n```"
            )
            result = result.replace(full_block, replacement, 1)
            repairs += 1

    if repairs:
        logger.info("repaired pd-patch counts in %d block(s)", repairs)
    return result, repairs
