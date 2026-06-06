"""
Embedding engine and store for semantic search and clustering.

Uses DashScope (OpenAI-compatible) embedding API with pure-Python
cosine similarity and k-means clustering.
"""

from __future__ import annotations

import json
import logging
import math
import random
import time
from pathlib import Path

from openai import OpenAI

from vidbrain.config import EmbeddingConfig

logger = logging.getLogger("vidbrain.embedding")


class EmbeddingEngine:
    """Calls the DashScope embedding API (OpenAI-compatible)."""

    def __init__(self, config: EmbeddingConfig) -> None:
        self._client = OpenAI(api_key=config.api_key, base_url=config.base_url)
        self._model = config.model

    def embed(self, text: str) -> list[float]:
        """Get a single embedding vector for *text*."""
        for attempt in range(3):
            try:
                resp = self._client.embeddings.create(
                    model=self._model, input=text
                )
                return resp.data[0].embedding
            except Exception:
                delay = 2**attempt  # 1s, 2s, 4s
                logger.warning(
                    "Embedding attempt %d/3 failed (retrying in %ds)",
                    attempt + 1,
                    delay,
                    exc_info=True,
                )
                if attempt == 2:
                    raise
                time.sleep(delay)

        # Should be unreachable — satisfy type-checker
        raise RuntimeError("embed: unreachable")

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Get embedding vectors for a batch of texts, splitting into
        sub-batches of 25 when necessary."""
        results: list[list[float]] = []
        batch_size = 25

        for start in range(0, len(texts), batch_size):
            chunk = texts[start : start + batch_size]
            vectors = self._call_api(chunk)
            results.extend(vectors)

        return results

    def _call_api(self, texts: list[str]) -> list[list[float]]:
        """Call the embedding API with retries."""
        for attempt in range(3):
            try:
                resp = self._client.embeddings.create(
                    model=self._model, input=texts
                )
                return [d.embedding for d in resp.data]
            except Exception:
                delay = 2**attempt  # 1s, 2s, 4s
                logger.warning(
                    "Embedding batch attempt %d/3 failed (retrying in %ds)",
                    attempt + 1,
                    delay,
                    exc_info=True,
                )
                if attempt == 2:
                    raise
                time.sleep(delay)

        raise RuntimeError("_call_api: unreachable")

    def similarity(self, vec1: list[float], vec2: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        dot = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = math.sqrt(sum(a * a for a in vec1))
        norm2 = math.sqrt(sum(b * b for b in vec2))
        if norm1 > 0 and norm2 > 0:
            return dot / (norm1 * norm2)
        return 0.0


class EmbeddingStore:
    """Local JSON cache for embedding vectors, keyed by video stem."""

    EMBEDDINGS_FILE = ".vidbrain_embeddings.json"

    def __init__(self, vault_path: str) -> None:
        self._vault_path = Path(vault_path)
        self._file_path = self._vault_path / self.EMBEDDINGS_FILE
        self._meta: dict = {}
        self._mtime: dict[str, str] = {}
        self._vectors: dict[str, list[float]] = {}
        self.load()

    # ── persistence ──────────────────────────────────────────────

    def load(self) -> None:
        """Load cached embeddings from disk, if the file exists."""
        fp = self._file_path
        if not fp.is_file():
            logger.debug("No embedding cache at %s — starting fresh", fp)
            return
        try:
            with fp.open("r", encoding="utf-8") as f:
                data = json.load(f)
            self._meta = data.get("meta", {})
            self._mtime = data.get("mtime", {})
            self._vectors = data.get("vectors", {})
            logger.debug(
                "Loaded %d vectors from %s", len(self._vectors), fp
            )
        except Exception:
            logger.warning("Failed to load embedding cache — ignoring", exc_info=True)

    def save(self) -> None:
        """Persist cached embeddings to disk."""
        self._meta["updated"] = time.time()
        payload = {
            "meta": self._meta,
            "mtime": self._mtime,
            "vectors": self._vectors,
        }
        self._vault_path.mkdir(parents=True, exist_ok=True)
        with self._file_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        logger.debug("Saved %d vectors to %s", len(self._vectors), self._file_path)

    # ── accessors ─────────────────────────────────────────────────

    def get_vector(self, stem: str) -> list[float] | None:
        return self._vectors.get(stem)

    def set_vector(self, stem: str, vector: list[float], mtime_str: str) -> None:
        self._vectors[stem] = vector
        self._mtime[stem] = mtime_str

    def needs_recompute(self, stem: str, file_mtime: str) -> bool:
        cached = self._mtime.get(stem)
        if cached is None:
            return True
        return file_mtime > cached

    def all_stems(self) -> list[str]:
        return list(self._vectors.keys())

    # ── similarity search ─────────────────────────────────────────

    def find_similar(
        self, query_vec: list[float], top_k: int = 5
    ) -> list[tuple[str, float]]:
        """Return the *top_k* stems most similar to *query_vec* by
        cosine similarity (O(N) brute-force)."""
        results: list[tuple[str, float]] = []
        for stem, vec in self._vectors.items():
            dot = sum(a * b for a, b in zip(query_vec, vec))
            norm1 = math.sqrt(sum(a * a for a in query_vec))
            norm2 = math.sqrt(sum(b * b for b in vec))
            sim = dot / (norm1 * norm2) if norm1 > 0 and norm2 > 0 else 0.0
            results.append((stem, sim))
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]


def _kmeans(
    vectors: list[list[float]], k: int, max_iter: int = 100
) -> list[int]:
    """Pure-Python k-means clustering using cosine similarity.

    Args:
        vectors: List of N vectors, each of dimension D.
        k: Number of clusters.
        max_iter: Maximum iterations before giving up.

    Returns:
        List of N cluster labels (integers in 0..k-1).
        Returns an empty list when *vectors* is empty or *k* <= 0.
    """
    if not vectors or k <= 0:
        return []

    n = len(vectors)
    if k >= n:
        return list(range(n))

    dim = len(vectors[0])
    rng = random.Random(42)
    indices = rng.sample(range(n), k)
    centroids = [vectors[i][:] for i in indices]

    labels = [0] * n
    for _iteration in range(max_iter):
        changed = False

        # ── assign each vector to nearest centroid ──
        for i, vec in enumerate(vectors):
            best_label = -1
            best_sim = -2.0
            for c_idx, centroid in enumerate(centroids):
                dot = sum(a * b for a, b in zip(vec, centroid))
                norm1 = math.sqrt(sum(a * a for a in vec))
                norm2 = math.sqrt(sum(b * b for b in centroid))
                sim = (
                    dot / (norm1 * norm2)
                    if norm1 > 0 and norm2 > 0
                    else 0.0
                )
                if sim > best_sim:
                    best_sim = sim
                    best_label = c_idx
            if labels[i] != best_label:
                changed = True
                labels[i] = best_label

        if not changed:
            break

        # ── recompute centroids ──
        new_centroids = [[0.0] * dim for _ in range(k)]
        counts = [0] * k
        for i, vec in enumerate(vectors):
            c = labels[i]
            counts[c] += 1
            for d in range(dim):
                new_centroids[c][d] += vec[d]
        for c in range(k):
            if counts[c] > 0:
                for d in range(dim):
                    new_centroids[c][d] /= counts[c]
        centroids = new_centroids

    return labels
