"""Golden dataset schema, loader, and validation."""

import json
from pathlib import Path
from typing import Any


class GoldenEntry:
    """A single entry in the golden evaluation dataset."""

    __slots__ = (
        "id", "question", "history", "query_type", "relevant_chunk_ids",
        "reference_answer", "difficulty", "notes",
    )

    def __init__(
        self,
        id: str,
        question: str,
        *,
        history: list[dict[str, str]] | None = None,
        query_type: str = "both",
        relevant_chunk_ids: list[str] | None = None,
        reference_answer: str = "",
        difficulty: str = "medium",
        notes: str = "",
    ):
        self.id = id
        self.question = question
        self.history = history or []
        self.query_type = query_type
        self.relevant_chunk_ids = relevant_chunk_ids or []
        self.reference_answer = reference_answer
        self.difficulty = difficulty
        self.notes = notes

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "GoldenEntry":
        return cls(
            id=d["id"],
            question=d["question"],
            history=d.get("history", []),
            query_type=d.get("query_type", "both"),
            relevant_chunk_ids=d.get("relevant_chunk_ids", []),
            reference_answer=d.get("reference_answer", ""),
            difficulty=d.get("difficulty", "medium"),
            notes=d.get("notes", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "history": self.history,
            "query_type": self.query_type,
            "relevant_chunk_ids": self.relevant_chunk_ids,
            "reference_answer": self.reference_answer,
            "difficulty": self.difficulty,
            "notes": self.notes,
        }


class GoldenDataset:
    """Loads and holds the golden evaluation dataset."""

    def __init__(self, path: Path | None = None):
        if path is None:
            path = Path(__file__).parent / "golden_dataset.json"
        self.path = path
        with open(path) as f:
            raw = json.load(f)
        self.entries: list[GoldenEntry] = [
            GoldenEntry.from_dict(item) for item in raw
        ]
        self._validate()

    def _validate(self) -> None:
        ids = set()
        for e in self.entries:
            if e.id in ids:
                raise ValueError(f"Duplicate dataset entry id: {e.id}")
            ids.add(e.id)
            if not e.question.strip():
                raise ValueError(f"Empty question in entry {e.id}")
            if e.query_type not in ("object_reference", "conceptual", "both"):
                raise ValueError(
                    f"Invalid query_type {e.query_type!r} in entry {e.id}"
                )

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self):
        return iter(self.entries)

    def by_type(self, query_type: str) -> list[GoldenEntry]:
        return [e for e in self.entries if e.query_type == query_type]

    def by_difficulty(self, difficulty: str) -> list[GoldenEntry]:
        return [e for e in self.entries if e.difficulty == difficulty]

    @property
    def stats(self) -> dict[str, Any]:
        types = {}
        diffs = {}
        for e in self.entries:
            types[e.query_type] = types.get(e.query_type, 0) + 1
            diffs[e.difficulty] = diffs.get(e.difficulty, 0) + 1
        return {
            "total_entries": len(self.entries),
            "by_query_type": types,
            "by_difficulty": diffs,
            "entries_with_relevant_chunks": sum(
                1 for e in self.entries if e.relevant_chunk_ids
            ),
            "entries_with_reference_answer": sum(
                1 for e in self.entries if e.reference_answer
            ),
            "multi_turn_count": sum(
                1 for e in self.entries if e.history
            ),
        }
