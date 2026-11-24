"""Serialize a validated PdPatch to Pd's native ``.pd`` text format.

A ``.pd`` file is a list of records, each ended by an unescaped ``;``. Box text
comes from the model, so ``escape_text`` guarantees it cannot end its record.

Connections are only dropped when they cannot be written (a missing box, or a
comment) or repeat another one. Other bad wires are kept on purpose: Pd reports
them, and hiding them would hide the mistake from whoever runs the patch.
"""

from __future__ import annotations

import re

from pd_schema import PdObject, PdPatch

# Complete creation arguments for each widget, in Pd's own save order. Pd ignores
# a partial argument list, so arguments in the box text are laid over these.
_WIDGET_ARGS = {
    "bng": "15 250 50 0 empty empty empty 17 7 0 10 -262144 -1 -1",
    "tgl": "15 0 empty empty empty 17 7 0 10 -262144 -1 -1 0 1",
    "hsl": "128 15 0 127 0 0 empty empty empty -2 -8 0 10 -262144 -1 -1 0 1",
    "vsl": "15 128 0 127 0 0 empty empty empty 0 -9 0 10 -262144 -1 -1 0 1",
    "hradio": "15 1 0 8 empty empty empty 0 -8 0 10 -262144 -1 -1 0",
    "vradio": "15 1 0 8 empty empty empty 0 -8 0 10 -262144 -1 -1 0",
    "nbx": "5 14 -1e+37 1e+37 0 0 empty empty empty 0 -8 0 10 -262144 -1 -1 0 256",
    "vu": "15 120 empty empty -1 -8 0 10 -66577 -1 1 0",
    "cnv": "15 100 60 empty empty empty 20 12 0 14 -233017 -66577 0",
}
_WIDGET_SIZE = {
    "bng": (15, 15), "tgl": (15, 15), "hsl": (128, 15), "vsl": (15, 128),
    "hradio": (120, 15), "vradio": (15, 120), "nbx": (60, 20), "vu": (15, 120),
    "cnv": (100, 60),
}
_ARRAY_FUNCTIONS = {
    "define", "size", "sum", "get", "set", "quantile", "random", "max", "min",
}

_NUMBER_RE = re.compile(r"[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?")
_CHAR_W = 7
_BOX_H = 20
_GAP_X = 25
_GAP_Y = 30
_MARGIN = 30


def escape_text(text: str) -> str:
    """Escape box text for a record; accepts text that is already escaped.

    ``,`` and ``;`` are separate tokens in Pd, so they are spaced out, and all
    whitespace (including newlines) collapses to single spaces.
    """
    out: list[str] = []
    i = 0
    while i < len(text):
        char = text[i]
        if char == "\\" and i + 1 < len(text) and text[i + 1] in ",;$\\ ":
            i += 1
            char = text[i]
            if char == " ":
                out.append("\\ ")
                i += 1
                continue
        if char in ",;":
            out.append(f" \\{char} ")
        elif char in "$\\":
            out.append("\\" + char)
        else:
            out.append(char)
        i += 1
    # Collapse whitespace, except a space that is itself escaped.
    return re.sub(r"(\\\\|\\ )|\s+", lambda m: m.group(1) or " ", "".join(out)).strip(" ")


def _widget_text(box_type: str, text: str) -> str:
    args = _WIDGET_ARGS[box_type].split()
    given = text.split()
    if given and given[0] == box_type:
        given = given[1:]
    # Use the given arguments only if each fits its slot; a label such as
    # "start" is not a creation argument.
    if len(given) <= len(args) and all(
        _NUMBER_RE.fullmatch(new) if _NUMBER_RE.fullmatch(old) else True
        for new, old in zip(given, args)
    ):
        args[: len(given)] = given
    return escape_text(" ".join([box_type, *args]))


def _record(obj: PdObject, x: int, y: int) -> str:
    text = obj.text.strip()
    if obj.type == "floatatom":
        # A number box's value is not part of a Pd file; only its settings are.
        return f"#X floatatom {x} {y} 5 0 0 0 - - -;"
    if obj.type in _WIDGET_ARGS:
        return f"#X obj {x} {y} {_widget_text(obj.type, text)};"
    if obj.type in ("table", "array"):
        words = text.split()
        if not words or words[0] != obj.type:
            words.insert(0, obj.type)
        if obj.type == "array" and len(words) > 1 and words[1] not in _ARRAY_FUNCTIONS:
            words.insert(1, "define")
        text = " ".join(words)
    keyword = {"msg": "msg", "comment": "text"}.get(obj.type, "obj")
    return f"#X {keyword} {x} {y} {escape_text(text)}".rstrip(" ") + ";"


def _ranks(ids: list[str], edges: list[tuple[str, str]]) -> dict[str, int]:
    """Longest-path rank of each box, ignoring the wires that close a loop."""
    children: dict[str, list[str]] = {i: [] for i in ids}
    for src, dst in edges:
        children[src].append(dst)

    order: list[str] = []
    state: dict[str, int] = {}  # 1 = on the stack, 2 = finished
    forward: list[tuple[str, str]] = []
    for root in ids:
        if root in state:
            continue
        state[root] = 1
        stack = [(root, iter(children[root]))]
        while stack:
            node, pending = stack[-1]
            for child in pending:
                if state.get(child) == 1:
                    continue  # closes a loop
                forward.append((node, child))
                if child not in state:
                    state[child] = 1
                    stack.append((child, iter(children[child])))
                    break
            else:
                state[node] = 2
                order.append(node)
                stack.pop()

    kept: dict[str, list[str]] = {i: [] for i in ids}
    for src, dst in forward:
        kept[src].append(dst)
    rank = dict.fromkeys(ids, 0)
    for node in reversed(order):
        for child in kept[node]:
            rank[child] = max(rank[child], rank[node] + 1)
    return rank


def _positions(patch: PdPatch, index: dict[str, int]) -> list[tuple[int, int]]:
    rank = _ranks(
        list(index),
        [(c.srcId, c.dstId) for c in patch.connections if c.srcId in index and c.dstId in index],
    )
    rows: dict[int, list[int]] = {}
    for i, obj in enumerate(patch.objects):
        rows.setdefault(rank[obj.id], []).append(i)

    positions = [(0, 0)] * len(patch.objects)
    y = _MARGIN
    for row in sorted(rows):
        x, height = _MARGIN, _BOX_H
        for i in rows[row]:
            obj = patch.objects[i]
            w, h = _WIDGET_SIZE.get(
                obj.type, (max(40, len(obj.text) * _CHAR_W + 10), _BOX_H)
            )
            positions[i] = (x, y)
            x += w + _GAP_X
            height = max(height, h)
        y += height + _GAP_Y
    return positions


def to_pd(patch: PdPatch) -> str:
    """Return the contents of a ``.pd`` file for ``patch``."""
    index = {obj.id: i for i, obj in enumerate(patch.objects)}
    comments = {obj.id for obj in patch.objects if obj.type == "comment"}
    lines = ["#N canvas 100 100 700 500 12;"]
    for obj, (x, y) in zip(patch.objects, _positions(patch, index)):
        lines.append(_record(obj, x, y))

    wires = dict.fromkeys(
        f"#X connect {index[c.srcId]} {c.srcOutlet} {index[c.dstId]} {c.dstInlet};"
        for c in patch.connections
        if c.srcId in index and c.dstId in index and not {c.srcId, c.dstId} & comments
    )
    return "\n".join([*lines, *wires]) + "\n"
