"""Tests for serializing PdPatch to .pd files (TASK-5.3)."""

import os
import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from pd_file import escape_text, to_pd
from pd_schema import PD_OBJECT_TYPES, PdPatch

PD = os.environ.get("PD_BIN") or shutil.which("pd") or shutil.which("puredata")

BACKSLASH = chr(92)

# type -> (text, inlets, outlets)
BOXES = {
    "obj": ("osc~ 440", 2, 1),
    "msg": ("0, $1 100; pd dsp 0", 1, 1),
    "floatatom": ("440", 1, 1),
    "comment": ("a comment; with, $1 punctuation", 0, 0),
    "table": ("table wave 512", 0, 0),
    "array": ("array define a1 100", 1, 1),
    "bng": ("bng", 1, 1),
    "tgl": ("tgl", 1, 1),
    "hsl": ("hsl 128 15 0 127", 1, 1),
    "vsl": ("vsl", 1, 1),
    "hradio": ("hradio", 1, 1),
    "vradio": ("vradio", 1, 1),
    "nbx": ("nbx", 1, 1),
    "vu": ("vu", 2, 2),
    "cnv": ("cnv", 0, 0),
}


def make(objects, wires=()):
    return PdPatch.model_validate({
        "objects": [
            {"id": i, "type": t, "text": text, "inlets": ins, "outlets": outs}
            for i, t, text, ins, outs in objects
        ],
        "connections": [
            {"srcId": a, "srcOutlet": ao, "dstId": b, "dstInlet": bi}
            for a, ao, b, bi in wires
        ],
    })


def one(box_type, text=None):
    default, ins, outs = BOXES[box_type]
    return make([("0", box_type, default if text is None else text, ins, outs)])


def records(pd_text):
    """Split on unescaped semicolons, as Pd does."""
    return [r.strip() for r in re.split(r"(?<!\\);", pd_text) if r.strip()]


def test_every_schema_type_is_covered():
    assert set(BOXES) == set(PD_OBJECT_TYPES)


@pytest.mark.parametrize("box_type", BOXES)
def test_each_type_is_one_record(box_type):
    recs = records(to_pd(one(box_type)))
    assert len(recs) == 2
    keyword = {"msg": "msg", "floatatom": "floatatom", "comment": "text"}.get(box_type, "obj")
    assert re.match(rf"#X {keyword} \d+ \d+ ", recs[1])


def test_connections_use_box_indices():
    patch = make(
        [("a", "obj", "osc~ 440", 2, 1), ("b", "obj", "dac~", 2, 0)],
        [("a", 0, "b", 0), ("a", 0, "b", 1)],
    )
    assert records(to_pd(patch))[-2:] == ["#X connect 0 0 1 0", "#X connect 0 0 1 1"]


def test_unwritable_and_duplicate_wires_are_dropped():
    patch = make(
        [("a", "obj", "f", 2, 1), ("c", "comment", "hi", 0, 0), ("b", "obj", "print", 1, 0)],
        [("a", 0, "b", 0), ("a", 0, "b", 0), ("a", 0, "nope", 0), ("c", 0, "b", 0)],
    )
    assert to_pd(patch).count("#X connect") == 1


def test_loops_are_serialized():
    patch = make(
        [("a", "obj", "f", 2, 1), ("b", "obj", "+ 1", 2, 1)],
        [("a", 0, "b", 0), ("b", 0, "a", 1), ("a", 0, "a", 1)],
    )
    assert to_pd(patch).count("#X connect") == 3


def test_wired_boxes_are_placed_below_their_source():
    patch = make(
        [("b", "obj", "print", 1, 0), ("a", "vsl", "vsl", 1, 1)], [("a", 0, "b", 0)]
    )
    ys = [int(r.split()[3]) for r in records(to_pd(patch))[1:3]]
    assert ys[0] > ys[1] + 128


@pytest.mark.parametrize("raw, expected", [
    ("0, 4 2000", r"0 \, 4 2000"),
    (r"0 \, 4 2000", r"0 \, 4 2000"),
    ("; pd dsp 1", r"\; pd dsp 1"),
    ("expr $f1*2", r"expr \$f1*2"),
    (r"set \$1", r"set \$1"),
    ("a\nb\t c", "a b c"),
    (r"a\ b", r"a\ b"),
    ("end\\", r"end\\"),
    (r"end\\", r"end\\"),
])
def test_escape_text(raw, expected):
    assert escape_text(raw) == expected


@pytest.mark.parametrize("box_type", BOXES)
@pytest.mark.parametrize("attack", [
    "x;\n#X obj 1 1 evil", "x" + BACKSLASH + ";#X obj 1 1 evil",
    "x" + BACKSLASH * 2 + ";#X obj 1 1 evil", "x" + BACKSLASH,
])
def test_text_cannot_inject_records(box_type, attack):
    body = to_pd(one(box_type, f"{box_type} {attack}"))
    # Walk escapes exactly: an even run of backslashes leaves ';' unescaped.
    assert len(re.findall(r"(?<!\\)(?:\\\\)*;", body)) == 2


def test_widget_arguments():
    def args(text):
        return records(to_pd(one("hsl", text)))[1].split()[4:]

    full = args("hsl")
    assert len(full) == 19
    assert args("hsl 200 15 20 2000")[1:5] == ["200", "15", "20", "2000"]
    assert args("frequency") == full
    assert args("hsl " + " ".join(["1"] * 30)) == full


def test_data_store_text_is_normalised():
    assert records(to_pd(one("table", "wave 64")))[1].endswith("table wave 64")
    assert records(to_pd(one("array", "array a1 100")))[1].endswith("array define a1 100")
    assert records(to_pd(one("array", "array get a1")))[1].endswith("array get a1")


# --- Headless Pd ---


def load_in_pd(tmp_path, patch):
    path = tmp_path / "patch.pd"
    path.write_text(to_pd(patch))
    result = subprocess.run(
        [PD, "-nogui", "-noaudio", "-nomidi", "-noprefs", "-stderr",
         "-open", str(path), "-send", "pd quit"],
        capture_output=True, text=True, timeout=30,
    )
    return result.stdout + result.stderr


ERRORS = re.compile(r"couldn't create|connection failed|error|can't|no such", re.IGNORECASE)


@pytest.fixture
def needs_pd():
    if not PD:
        # CI installs Pd, so a missing binary there must not pass silently.
        assert not os.environ.get("CI"), "Pd is not installed"
        pytest.skip("Pd is not installed (set PD_BIN)")


class TestHeadlessPd:
    @pytest.mark.parametrize("box_type", BOXES)
    def test_each_type_loads(self, needs_pd, tmp_path, box_type):
        text, ins, outs = BOXES[box_type]
        objects = [
            ("load", "obj", "loadbang", 0, 1), ("say", "msg", "patch-loaded", 1, 1),
            ("print", "obj", "print", 1, 0), ("x", box_type, text, ins, outs),
        ]
        wires = [("load", 0, "say", 0), ("say", 0, "print", 0)]
        for i in range(ins):
            objects.append((f"f{i}", "obj", "f", 2, 1))
            wires.append((f"f{i}", 0, "x", i))
        for o in range(outs):
            objects.append((f"o{o}", "obj", "snapshot~" if box_type == "obj" else "t a", 1, 1))
            wires.append(("x", o, f"o{o}", 0))
        out = load_in_pd(tmp_path, make(objects, wires))
        assert "patch-loaded" in out, out
        assert not ERRORS.search(out), out

    def test_escaped_message_and_expr_work(self, needs_pd, tmp_path):
        out = load_in_pd(tmp_path, make(
            [("l", "obj", "loadbang", 0, 1), ("m", "msg", "3, 4", 1, 1),
             ("e", "obj", "expr if($f1 > 3, $f1 * 10, 7)", 1, 1), ("p", "obj", "print", 1, 0)],
            [("l", 0, "m", 0), ("m", 0, "e", 0), ("e", 0, "p", 0)],
        ))
        assert re.search(r"print: 7\b", out) and re.search(r"print: 40\b", out), out
        assert not ERRORS.search(out), out

    def test_check_detects_bad_patches(self, needs_pd, tmp_path):
        bad_object = load_in_pd(tmp_path, make([("x", "obj", "nosuchobject~", 1, 1)]))
        assert ERRORS.search(bad_object), bad_object
        bad_wire = load_in_pd(tmp_path, make(
            [("a", "obj", "f", 2, 1), ("b", "obj", "print", 1, 0)], [("a", 0, "b", 5)]
        ))
        assert ERRORS.search(bad_wire), bad_wire


# --- Download endpoint ---


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("ALLOWED_ORIGIN", "http://localhost:5173")
    monkeypatch.setenv("CHAT_API_KEYS", "test-key")
    import main

    monkeypatch.setattr(main, "CHAT_API_KEYS", {"test-key"})
    main.limiter.reset()
    return TestClient(main.app)


class TestDownloadEndpoint:
    HEADERS = {"X-API-Key": "test-key"}

    def test_returns_pd_file(self, client):
        patch = one("bng")
        res = client.post("/patch/pd", json=patch.model_dump(), headers=self.HEADERS)
        assert res.status_code == 200
        assert res.text == to_pd(patch)
        assert 'filename="patch.pd"' in res.headers["content-disposition"]

    def test_requires_api_key(self, client):
        body = one("bng").model_dump()
        assert client.post("/patch/pd", json=body).status_code in (401, 403)
        assert client.post(
            "/patch/pd", json=body, headers={"X-API-Key": "wrong"}
        ).status_code == 403

    def test_rejects_invalid_patch(self, client):
        res = client.post(
            "/patch/pd", json={"objects": [], "connections": []}, headers=self.HEADERS
        )
        assert res.status_code == 422

    def test_accepts_more_objects_than_the_model_may_emit(self, client):
        body = {"objects": one("bng").model_dump()["objects"] * 20, "connections": []}
        for i, obj in enumerate(body["objects"]):
            body["objects"][i] = {**obj, "id": str(i)}
        res = client.post("/patch/pd", json=body, headers=self.HEADERS)
        assert res.status_code == 200
        assert res.text.count("#X obj") == 20
