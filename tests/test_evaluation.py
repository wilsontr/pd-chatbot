"""Pytest tests for the evaluation harness.

These tests assert minimum quality thresholds and serve as CI regression gates.
"""

import pytest

from evaluation.dataset import GoldenDataset, GoldenEntry
from evaluation.retrieval import run_retrieval_eval


@pytest.fixture(scope="module")
def dataset() -> GoldenDataset:
    return GoldenDataset()


# --- Dataset integrity tests (fast, no API calls) ---

def test_dataset_loads(dataset: GoldenDataset):
    assert len(dataset.entries) >= 10, "Golden dataset should have at least 10 entries"


def test_dataset_has_all_query_types(dataset: GoldenDataset):
    types = set(e.query_type for e in dataset.entries)
    assert "object_reference" in types, "Missing object_reference questions"
    assert "conceptual" in types, "Missing conceptual questions"
    assert "both" in types, "Missing mixed-type questions"


def test_dataset_has_multi_turn(dataset: GoldenDataset):
    multi = [e for e in dataset.entries if e.history]
    assert len(multi) >= 1, "Should have at least one multi-turn question"


def test_dataset_has_relevance_annotations(dataset: GoldenDataset):
    annotated = [e for e in dataset.entries if e.relevant_chunk_ids]
    assert len(annotated) >= 5, (
        f"At least 5 entries should have relevant_chunk_ids, got {len(annotated)}"
    )


def test_dataset_has_reference_answers(dataset: GoldenDataset):
    with_ref = [e for e in dataset.entries if e.reference_answer]
    assert len(with_ref) >= 5, (
        f"At least 5 entries should have reference_answers, got {len(with_ref)}"
    )


def test_dataset_no_duplicate_ids(dataset: GoldenDataset):
    ids = [e.id for e in dataset.entries]
    assert len(ids) == len(set(ids)), "Dataset contains duplicate entry IDs"


def test_dataset_valid_query_types(dataset: GoldenDataset):
    for e in dataset.entries:
        assert e.query_type in ("object_reference", "conceptual", "both"), (
            f"Invalid query_type in {e.id}: {e.query_type}"
        )


def test_dataset_valid_difficulty(dataset: GoldenDataset):
    valid = {"easy", "medium", "hard"}
    for e in dataset.entries:
        assert e.difficulty in valid, f"Invalid difficulty in {e.id}: {e.difficulty}"


# --- Retrieval evaluation tests (require API keys) ---

@pytest.mark.slow
def test_retrieval_recall_threshold(dataset: GoldenDataset):
    """Hybrid RRF retrieval must achieve minimum recall@5."""
    result = run_retrieval_eval(dataset, ablation=False, verbose=False)
    hybrid = result.get("hybrid_rrf", {})
    recall = hybrid.get("recall", {})
    recall5 = recall.get("recall@5", 0)
    assert recall5 >= 0.60, (
        f"Hybrid RRF recall@5 is {recall5:.4f}, below threshold of 0.60"
    )


@pytest.mark.slow
def test_retrieval_mrr_threshold(dataset: GoldenDataset):
    """Hybrid RRF must achieve minimum MRR."""
    result = run_retrieval_eval(dataset, ablation=False, verbose=False)
    hybrid = result.get("hybrid_rrf", {})
    mrr = hybrid.get("mrr", 0)
    assert mrr >= 0.30, (
        f"Hybrid RRF MRR is {mrr:.4f}, below threshold of 0.30"
    )


@pytest.mark.slow
def test_hybrid_beats_vector_only(dataset: GoldenDataset):
    """Prove hybrid RRF outperforms vector-only retrieval."""
    result = run_retrieval_eval(dataset, ablation=True, verbose=False)
    hybrid = result.get("hybrid_rrf", {})
    vector = result.get("vector_only", {})

    h_recall5 = hybrid.get("recall", {}).get("recall@5", 0)
    v_recall5 = vector.get("recall", {}).get("recall@5", 0)

    assert h_recall5 >= v_recall5, (
        f"Hybrid recall@5 ({h_recall5:.4f}) should be >= vector-only ({v_recall5:.4f})"
    )


@pytest.mark.slow
def test_hybrid_beats_bm25_only(dataset: GoldenDataset):
    """Prove hybrid RRF outperforms BM25-only retrieval."""
    result = run_retrieval_eval(dataset, ablation=True, verbose=False)
    hybrid = result.get("hybrid_rrf", {})
    bm25 = result.get("bm25_only", {})

    h_recall5 = hybrid.get("recall", {}).get("recall@5", 0)
    b_recall5 = bm25.get("recall", {}).get("recall@5", 0)

    assert h_recall5 >= b_recall5, (
        f"Hybrid recall@5 ({h_recall5:.4f}) should be >= BM25-only ({b_recall5:.4f})"
    )
