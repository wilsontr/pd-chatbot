"""Tests for exporting rated feedback as golden-dataset candidates (TASK-2)."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from evaluation.dataset import GoldenDataset, GoldenEntry
from evaluation.feedback_export import (
    DEFAULT_PARENT_LOOKUP,
    GOLDEN_NAME_RE,
    GoldenSetWriteRefused,
    build_candidate,
    export_candidates,
    is_golden_path,
    load_parent_index,
    main,
    select_rows,
    write_candidates,
)
from feedback_store import FeedbackStore

SOURCES = [
    {
        "heading_path": "osc~",
        "url": "http://msp.ucsd.edu/osc.htm",
        "source": "iem_reference",
        "content_type": "object_reference",
        "object_name": "osc~",
    }
]
INDEX = {("osc~", "http://msp.ucsd.edu/osc.htm", "iem_reference"): "iem_parent_158"}


@pytest.fixture
def store(tmp_path):
    return FeedbackStore(f"sqlite:///{tmp_path / 'fb.db'}")


def _rate(store, question, rating, answer="an answer", sources=SOURCES, comment=""):
    mid = store.save_response(question, answer, sources, "object_reference")
    assert store.save_rating(mid, rating, 1, comment)
    return mid


class TestCandidateShape:
    def test_shape_matches_golden_dataset_entry(self):
        row = {
            "message_id": "0123456789abcdef",
            "question": "What are the inlets of [osc~]?",
            "answer": "Two inlets.",
            "sources": SOURCES,
            "query_type": "object_reference",
            "rating": "down",
            "comment": "wrong inlet order",
            "timestamp": 1,
            "rated_at": datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc),
        }
        candidate = build_candidate(row, index=INDEX, used_ids=set())
        assert set(candidate) == set(GoldenEntry(id="x", question="x").to_dict())
        assert candidate["question"] == row["question"]
        assert candidate["relevant_chunk_ids"] == ["iem_parent_158"]
        assert candidate["difficulty"] == "hard"
        assert candidate["history"] == []
        assert candidate["reference_answer"] == ""  # a human writes this
        assert "0123456789abcdef" in candidate["notes"]
        assert "wrong inlet order" in candidate["notes"]
        assert "Two inlets." in candidate["notes"]

    def test_upvote_is_not_labelled_hard(self):
        row = {
            "message_id": "deadbeef", "question": "q", "answer": "a",
            "sources": SOURCES, "query_type": "conceptual", "rating": "up",
            "comment": "", "timestamp": 1,
            "rated_at": datetime(2026, 11, 20, tzinfo=timezone.utc),
        }
        candidate = build_candidate(row, index=INDEX, used_ids=set())
        assert candidate["difficulty"] == "medium"

    def test_unknown_query_type_falls_back_to_both(self):
        row = {
            "message_id": "abc", "question": "q", "answer": "a",
            "sources": [], "query_type": "", "rating": "down",
            "comment": "", "timestamp": 1, "rated_at": None,
        }
        candidate = build_candidate(row, index=INDEX, used_ids=set())
        assert candidate["query_type"] == "both"

    def test_unresolvable_sources_are_noted_not_guessed(self):
        row = {
            "message_id": "abc", "question": "q", "answer": "a",
            "sources": [{"heading_path": "nowhere", "url": "u", "source": "s"}],
            "query_type": "both", "rating": "down", "comment": "",
            "timestamp": 1, "rated_at": None,
        }
        candidate = build_candidate(row, index=INDEX, used_ids=set())
        assert candidate["relevant_chunk_ids"] == []
        assert "could not resolve" in candidate["notes"].lower()

    def test_ids_are_unique_per_row(self):
        used: set[str] = set()
        row = {
            "message_id": "ab" * 16, "question": "q", "answer": "a",
            "sources": [], "query_type": "both", "rating": "down",
            "comment": "", "timestamp": 1, "rated_at": None,
        }
        first = build_candidate(row, index=INDEX, used_ids=used)
        second = build_candidate(row, index=INDEX, used_ids=used)
        assert first["id"] != second["id"]


class TestFilters:
    NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)

    def _rows(self):
        return [
            {"rating": "up", "rated_at": self.NOW},
            {"rating": "down", "rated_at": self.NOW + timedelta(days=2)},
            {"rating": "down", "rated_at": None},  # unparseable / missing timestamp
        ]

    def test_no_filter_keeps_everything(self):
        assert len(select_rows(self._rows())) == 3

    def test_rating_filter(self):
        rows = select_rows(self._rows(), rating="down")
        assert [r["rating"] for r in rows] == ["down", "down"]

    def test_since_is_inclusive(self):
        rows = select_rows(self._rows(), since=self.NOW)
        assert len(rows) == 2

    def test_until_is_exclusive(self):
        rows = select_rows(self._rows(), until=self.NOW + timedelta(days=2))
        assert len(rows) == 1  # only the NOW row; the +2d row is excluded

    def test_date_filter_drops_rows_without_a_timestamp(self):
        assert all(r["rated_at"] for r in select_rows(self._rows(), since=self.NOW))

    def test_naive_sqlite_timestamps_are_treated_as_utc(self):
        rows = select_rows(
            [{"rating": "up", "rated_at": datetime(2026, 11, 20, 12, 0)}],
            since=self.NOW,
        )
        assert len(rows) == 1

    def test_store_rows_carry_a_server_timestamp(self, store):
        _rate(store, "q", "down")
        (row,) = store.get_feedback()
        assert row["rated_at"] is not None


class TestExportAgainstStore:
    def test_export_only_includes_matching_ratings(self, store):
        _rate(store, "good q", "up")
        _rate(store, "bad q", "down")
        candidates = export_candidates(store, rating="down", index=INDEX)
        assert [c["question"] for c in candidates] == ["bad q"]

    def test_same_question_rated_twice_yields_two_candidates(self, store):
        _rate(store, "What is [osc~]?", "up")
        _rate(store, "What is [osc~]?", "down")
        candidates = export_candidates(store, index=INDEX)
        assert len({c["id"] for c in candidates}) == 2
        assert {c["question"] for c in candidates} == {"What is [osc~]?"}

    def test_exported_file_loads_with_the_dataset_loader(self, store, tmp_path):
        _rate(store, "What is [osc~]?", "down")
        candidates = export_candidates(store, index=INDEX)
        out = write_candidates(candidates, tmp_path / "candidates.json")
        dataset = GoldenDataset(out)  # validates ids, question, query_type
        assert len(dataset) == 1
        assert dataset.entries[0].relevant_chunk_ids == ["iem_parent_158"]

    def test_empty_selection_writes_an_empty_list(self, store, tmp_path):
        out = write_candidates(export_candidates(store, index=INDEX), tmp_path / "c.json")
        assert json.loads(out.read_text()) == []


class TestGoldSetSeparation:
    @pytest.mark.parametrize(
        "name",
        ["golden_dataset.json", "golden_dataset_hard_batch3.json", "GOLDEN_DATASET_x.JSON"],
    )
    def test_golden_filenames_are_recognised(self, name):
        assert GOLDEN_NAME_RE.match(name)
        assert is_golden_path(Path("/tmp") / name)

    def test_refuses_to_write_the_golden_dataset(self, tmp_path):
        target = tmp_path / "golden_dataset_new.json"
        with pytest.raises(GoldenSetWriteRefused):
            write_candidates([{"id": "x"}], target)
        assert not target.exists()

    def test_review_file_is_a_different_file(self, tmp_path):
        golden = tmp_path / "golden_dataset.json"
        golden.write_text("[]")
        out = write_candidates([], tmp_path / "feedback_candidates.json")
        assert out != golden
        assert golden.read_text() == "[]"


class TestCli:
    def _seed(self, store):
        _rate(store, "downvoted question", "down", comment="hallucinated inlet")
        _rate(store, "upvoted question", "up")

    def test_cli_writes_only_downs_when_asked(self, store, tmp_path, capsys):
        self._seed(store)
        out = tmp_path / "review.json"
        code = main([
            "--database-url", str(store.engine.url),
            "--parent-lookup", str(DEFAULT_PARENT_LOOKUP),
            "--output", str(out),
            "--rating", "down",
        ])
        assert code == 0
        entries = json.loads(out.read_text())
        assert [e["question"] for e in entries] == ["downvoted question"]
        assert "1 candidate" in capsys.readouterr().out

    def test_cli_rejects_the_golden_set_as_output(self, store, tmp_path, capsys):
        self._seed(store)
        target = tmp_path / "golden_dataset_candidates.json"
        code = main([
            "--database-url", str(store.engine.url),
            "--parent-lookup", str(DEFAULT_PARENT_LOOKUP),
            "--output", str(target),
        ])
        assert code == 2
        assert "refusing to write" in capsys.readouterr().err
        assert not target.exists()

    def test_cli_date_range_uses_utc_days(self, store, tmp_path):
        self._seed(store)
        today = datetime.now(timezone.utc).date()
        out = tmp_path / "review.json"
        main([
            "--database-url", str(store.engine.url),
            "--parent-lookup", str(DEFAULT_PARENT_LOOKUP),
            "--output", str(out),
            "--since", today.isoformat(),
            "--until", today.isoformat(),
        ])
        assert len(json.loads(out.read_text())) == 2
        main([
            "--database-url", str(store.engine.url),
            "--parent-lookup", str(DEFAULT_PARENT_LOOKUP),
            "--output", str(out),
            "--until", (today - timedelta(days=1)).isoformat(),
        ])
        assert json.loads(out.read_text()) == []


class TestRealChunkIndex:
    def test_every_parent_chunk_is_resolvable_by_its_source_triple(self):
        index = load_parent_index(DEFAULT_PARENT_LOOKUP)
        parents = json.loads(DEFAULT_PARENT_LOOKUP.read_text())
        missing = [c["id"] for c in parents.values() if c["id"] not in index.values()]
        assert missing == []

    def test_known_object_reference_resolves_to_a_parent_id(self):
        index = load_parent_index(DEFAULT_PARENT_LOOKUP)
        parents = json.loads(DEFAULT_PARENT_LOOKUP.read_text())
        chunk = parents["iem_parent_158"]
        candidate = build_candidate(
            {
                "message_id": "abc", "question": chunk["heading_path"], "answer": "a",
                "sources": [chunk], "query_type": "object_reference",
                "rating": "down", "comment": "", "timestamp": 1, "rated_at": None,
            },
            index=index,
            used_ids=set(),
        )
        assert candidate["relevant_chunk_ids"] == ["iem_parent_158"]
