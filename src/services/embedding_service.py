"""
Embedding engine and store for semantic search and clustering.

Uses DashScope (OpenAI-compatible) embedding API with numpy-accelerated
cosine similarity and k-means clustering.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import numpy as np
from openai import OpenAI

from src.models.config import EmbeddingConfig

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
                resp = self._client.embeddings.create(model=self._model, input=text)
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
            chunk = texts[start : start + batch_size]  # noqa: E203
            vectors = self._call_api(chunk)
            results.extend(vectors)

        return results

    def _call_api(self, texts: list[str]) -> list[list[float]]:
        """Call the embedding API with retries."""
        for attempt in range(3):
            try:
                resp = self._client.embeddings.create(model=self._model, input=texts)
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
        """Compute cosine similarity between two vectors (numpy-accelerated)."""
        a = np.asarray(vec1, dtype=np.float32)
        b = np.asarray(vec2, dtype=np.float32)
        dot = np.dot(a, b)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a > 0 and norm_b > 0:
            return float(dot / (norm_a * norm_b))
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
            logger.debug("Loaded %d vectors from %s", len(self._vectors), fp)
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
        cosine similarity (numpy-accelerated batch computation)."""
        if not self._vectors:
            return []

        stems = list(self._vectors.keys())
        # 构建 numpy 矩阵 (N x D)
        vecs = np.array([self._vectors[s] for s in stems], dtype=np.float32)
        query = np.asarray(query_vec, dtype=np.float32)

        # 批量计算余弦相似度
        dot = np.dot(vecs, query)
        norms_vecs = np.linalg.norm(vecs, axis=1)
        norm_q = np.linalg.norm(query)
        denom = norms_vecs * norm_q
        # 避免除零
        sims = np.divide(dot, denom, out=np.zeros_like(dot), where=denom > 0)

        # 获取 top-k 索引
        if top_k >= len(sims):
            top_indices = np.argsort(sims)[::-1]
        else:
            top_indices = np.argpartition(sims, -top_k)[-top_k:]
            top_indices = top_indices[np.argsort(sims[top_indices])[::-1]]

        return [(stems[i], float(sims[i])) for i in top_indices]


def _kmeans(vectors: list[list[float]], k: int, max_iter: int = 100) -> list[int]:
    """Numpy-accelerated k-means clustering using cosine similarity.

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

    # 构建 numpy 矩阵 (N x D)
    data = np.array(vectors, dtype=np.float32)
    dim = data.shape[1]

    # 随机初始化质心
    rng = np.random.RandomState(42)
    indices = rng.choice(n, k, replace=False)
    centroids = data[indices].copy()

    labels = np.zeros(n, dtype=np.int32)

    for _iteration in range(max_iter):
        # ── L2 归一化后用点积近似余弦相似度 ──
        data_norm = data / (np.linalg.norm(data, axis=1, keepdims=True) + 1e-10)
        centroids_norm = centroids / (
            np.linalg.norm(centroids, axis=1, keepdims=True) + 1e-10
        )

        # 余弦相似度矩阵 (N x K)
        sim = np.dot(data_norm, centroids_norm.T)
        new_labels = np.argmax(sim, axis=1)

        # 检查收敛
        if np.array_equal(labels, new_labels):
            break
        labels = new_labels

        # ── 重新计算质心 ──
        centroids = np.zeros((k, dim), dtype=np.float32)
        for c in range(k):
            mask = labels == c
            if mask.any():
                centroids[c] = data[mask].mean(axis=0)

    return labels.tolist()
