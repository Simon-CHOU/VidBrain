"""Extended tests for embedding service."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from src.models.config import EmbeddingConfig
from src.services.embedding_service import EmbeddingEngine, EmbeddingStore


@pytest.fixture
def embed_config(mock_env_dashscope) -> EmbeddingConfig:
    return EmbeddingConfig()


class TestEmbeddingEngine:
    @patch("src.services.embedding_service.OpenAI")
    def test_embed_success(self, mock_openai_cls, embed_config: EmbeddingConfig) -> None:
        client = MagicMock()
        mock_openai_cls.return_value = client
        client.embeddings.create.return_value = MagicMock(
            data=[MagicMock(embedding=[0.1, 0.2, 0.3])]
        )
        engine = EmbeddingEngine(embed_config)
        vec = engine.embed("hello")
        assert vec == [0.1, 0.2, 0.3]

    @patch("src.services.embedding_service.time.sleep")
    @patch("src.services.embedding_service.OpenAI")
    def test_embed_retries(self, mock_openai_cls, _sleep, embed_config: EmbeddingConfig) -> None:
        client = MagicMock()
        mock_openai_cls.return_value = client
        client.embeddings.create.side_effect = [
            RuntimeError("fail"),
            MagicMock(data=[MagicMock(embedding=[1.0, 0.0])]),
        ]
        engine = EmbeddingEngine(embed_config)
        vec = engine.embed("text")
        assert vec == [1.0, 0.0]

    @patch("src.services.embedding_service.OpenAI")
    def test_embed_batch(self, mock_openai_cls, embed_config: EmbeddingConfig) -> None:
        client = MagicMock()
        mock_openai_cls.return_value = client
        client.embeddings.create.return_value = MagicMock(
            data=[MagicMock(embedding=[1.0]), MagicMock(embedding=[0.0])]
        )
        engine = EmbeddingEngine(embed_config)
        vecs = engine.embed_batch(["a", "b"])
        assert len(vecs) == 2

    def test_similarity(self, embed_config: EmbeddingConfig) -> None:
        with patch("src.services.embedding_service.OpenAI"):
            engine = EmbeddingEngine(embed_config)
        assert engine.similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
        assert engine.similarity([0.0, 0.0], [1.0, 0.0]) == 0.0


class TestEmbeddingStore:
    def test_load_save_and_find_similar(self, tmp_path) -> None:
        store = EmbeddingStore(str(tmp_path))
        store.set_vector("a", [1.0, 0.0, 0.0], "2025-01-01")
        store.set_vector("b", [0.9, 0.1, 0.0], "2025-01-02")
        store.save()
        store2 = EmbeddingStore(str(tmp_path))
        assert store2.get_vector("a") == [1.0, 0.0, 0.0]
        assert store2.needs_recompute("a", "2024-01-01") is False
        assert store2.needs_recompute("a", "2025-02-01") is True
        similar = store2.find_similar([1.0, 0.0, 0.0], top_k=1)
        assert similar[0][0] == "a"

    def test_load_corrupt_file(self, tmp_path) -> None:
        bad = tmp_path / ".vidbrain_embeddings.json"
        bad.write_text("not json", encoding="utf-8")
        store = EmbeddingStore(str(tmp_path))
        assert store.all_stems() == []
