"""Tests for embedding service module."""

from __future__ import annotations

import numpy as np
from src.services.embedding_service import _kmeans


class TestKMeans:
    """Tests for _kmeans clustering function."""

    def test_empty_vectors(self) -> None:
        """Should return empty list for empty input."""
        assert _kmeans([], k=3) == []

    def test_k_zero_or_negative(self) -> None:
        """Should return empty list for k <= 0."""
        vectors = [[1.0, 0.0], [0.0, 1.0]]
        assert _kmeans(vectors, k=0) == []
        assert _kmeans(vectors, k=-1) == []

    def test_k_greater_than_n(self) -> None:
        """Should return range(n) when k >= n."""
        vectors = [[1.0, 0.0], [0.0, 1.0]]
        result = _kmeans(vectors, k=5)
        assert result == [0, 1]

    def test_basic_clustering(self) -> None:
        """Should produce valid labels for separated points."""
        # Two widely separated clusters
        vectors = [
            [10.0, 10.0],
            [10.1, 10.1],
            [10.0, 10.2],  # cluster A
            [-10.0, -10.0],
            [-10.1, -10.1],
            [-10.0, -10.2],  # cluster B
        ]
        labels = _kmeans(vectors, k=2)
        assert len(labels) == 6
        # All labels must be valid (in range 0..k-1)
        assert all(0 <= lbl < 2 for lbl in labels)
        # Each group should be internally consistent (same label within group)
        assert labels[0] == labels[1] == labels[2], f"First group not uniform: {labels[:3]}"
        assert labels[3] == labels[4] == labels[5], f"Second group not uniform: {labels[3:]}"

    def test_labels_are_valid_indices(self) -> None:
        """All labels should be in range 0..k-1."""
        rng = np.random.RandomState(0)
        vectors = rng.randn(30, 5).tolist()
        k = 4
        labels = _kmeans(vectors, k=k)
        assert all(0 <= lbl < k for lbl in labels)
        assert len(labels) == 30

    def test_reproducibility(self) -> None:
        """Should produce same results with same seed."""
        rng = np.random.RandomState(42)
        vectors = rng.randn(20, 3).tolist()
        labels1 = _kmeans(vectors, k=3)
        labels2 = _kmeans(vectors, k=3)
        assert labels1 == labels2
