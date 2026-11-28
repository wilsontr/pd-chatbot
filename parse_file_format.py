"""Add the Pd file-format specification to the corpus (TASK-5.41).

The Pd documentation in the corpus never describes the ``.pd`` patch format, so
answers about ``#X connect`` or ``#X restore`` lines had nothing to cite. The
source is the community *Unofficial PD v0.37 fileformat specification*
(Asselbergs and Sietsma, 2004, GNU FDL 1.2), stored with its licence notice in
``sources/pd_file_format.json``.

The other sources are rebuilt by ``chunk.py`` from parsed HTML that is not in the
repository, so this source is merged straight into the committed chunk files.
The merge is idempotent: it drops any earlier ``pd_file_format`` chunks first.

    python parse_file_format.py        # rewrites parent_chunks.json / child_chunks.json
    python embed_and_index.py          # then re-index as usual
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chunk import split_text

SOURCE = "pd_file_format"
SOURCE_FILE = Path(__file__).parent / "sources" / "pd_file_format.json"
LABEL = "Unofficial Pd 0.37 file format specification"


def load_source(path: Path = SOURCE_FILE) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def file_format_chunks(doc: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    """Parent and child chunks for the spec, one parent per section."""
    parents: list[dict] = []
    children: list[dict] = []
    notice = f"({doc['authors']}; {doc['applies_to']})"
    for section in doc["sections"]:
        heading = f"{LABEL} > {section['title']}"
        full_text = f"{heading}\n\n{section['text']}\n\n{notice}"
        parent_id = f"pdff_parent_{len(parents)}"
        parents.append({
            "id": parent_id,
            "text": full_text,
            "source": SOURCE,
            "content_type": "conceptual",
            "object_name": None,
            "heading_path": heading,
            "url": doc["url"],
        })
        for child_text in split_text(full_text):
            children.append({
                "id": f"pdff_child_{len(children)}",
                "parent_id": parent_id,
                "text": child_text,
                "source": SOURCE,
                "content_type": "conceptual",
                "object_name": None,
                "heading_path": heading,
                "url": doc["url"],
            })
    return parents, children


def merge(parents_path: Path, children_path: Path, doc: dict[str, Any]) -> tuple[int, int]:
    parents = [c for c in json.loads(parents_path.read_text()) if c.get("source") != SOURCE]
    children = [c for c in json.loads(children_path.read_text()) if c.get("source") != SOURCE]
    new_parents, new_children = file_format_chunks(doc)
    parents_path.write_text(json.dumps(parents + new_parents, indent=2))
    children_path.write_text(json.dumps(children + new_children, indent=2))
    return len(new_parents), len(new_children)


if __name__ == "__main__":
    root = Path(__file__).parent
    added = merge(root / "parent_chunks.json", root / "child_chunks.json", load_source())
    print(f"pd_file_format: {added[0]} parent and {added[1]} child chunks")
