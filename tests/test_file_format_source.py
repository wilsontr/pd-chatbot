"""The Pd file-format spec in the corpus (TASK-5.41)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from agent.retrieval import SOURCE_LABELS, format_excerpts
from parse_file_format import SOURCE, file_format_chunks, load_source, merge

ROOT = Path(__file__).resolve().parent.parent


def test_the_source_carries_its_licence_and_caveat():
    doc = load_source()
    assert "GNU Free Documentation License" in doc["license"]
    assert "Asselbergs" in doc["authors"] and "Pd 0.37" in doc["applies_to"]
    titles = {s["title"] for s in doc["sections"]}
    assert {"Wiring", "Reference > connect", "Reference > restore",
            "Reference > floatatom", "Reference > text"} <= titles


def test_every_chunk_names_the_source_and_its_age():
    parents, children = file_format_chunks(load_source())
    assert parents and children
    for chunk in parents + children:
        assert chunk["source"] == SOURCE
        assert chunk["heading_path"].startswith("Unofficial Pd 0.37 file format specification")
    for parent in parents:
        assert "unofficial and may not describe records added in later versions" in parent["text"]
    assert {c["parent_id"] for c in children} <= {p["id"] for p in parents}


def test_the_committed_corpus_holds_it_exactly_once():
    parents = json.loads((ROOT / "parent_chunks.json").read_text())
    expected, _ = file_format_chunks(load_source())
    mine = [c for c in parents if c["source"] == SOURCE]
    assert mine == expected


def test_merge_is_idempotent_and_leaves_other_sources_alone(tmp_path):
    for name in ("parent_chunks.json", "child_chunks.json"):
        shutil.copy(ROOT / name, tmp_path / name)
    before = (tmp_path / "parent_chunks.json").read_text()
    merge(tmp_path / "parent_chunks.json", tmp_path / "child_chunks.json", load_source())
    assert (tmp_path / "parent_chunks.json").read_text() == before


def test_excerpts_are_labelled_by_source():
    assert set(SOURCE_LABELS) == {"msp_manual", "iem_reference", "puckette_book", SOURCE}
    text = format_excerpts([{"source": SOURCE, "heading_path": "h", "text": "t", "url": "u"},
                            {"source": "puckette_book", "heading_path": "b", "text": "t", "url": "v"}])
    assert "[Unofficial Pd 0.37 file format specification: h]" in text
    assert "[Puckette, Theory and Technique of Electronic Music: b]" in text
