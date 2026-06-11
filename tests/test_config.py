"""Tests for config models."""

from __future__ import annotations

import os

import pytest
from src.models.config import EmbeddingConfig, LLMConfig, PipelineConfig


class TestLLMConfig:
    """Tests for LLMConfig dataclass."""

    def test_raises_when_api_key_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should raise OSError when DEEPSEEK_API_KEY is not set."""
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
        with pytest.raises(OSError, match="DEEPSEEK_API_KEY"):
            LLMConfig()

    def test_raises_when_base_url_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should raise OSError when DEEPSEEK_BASE_URL is not set."""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
        monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
        with pytest.raises(OSError, match="DEEPSEEK_BASE_URL"):
            LLMConfig()

    def test_loads_from_env(self, mock_env_deepseek: None) -> None:
        """Should load API key and base URL from environment."""
        config = LLMConfig()
        assert config.api_key == "sk-test-key-12345"
        assert config.base_url == "https://api.deepseek.com/v1"
        assert config.model == "deepseek-v4-flash"


class TestEmbeddingConfig:
    """Tests for EmbeddingConfig dataclass."""

    def test_raises_when_api_key_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should raise OSError when DASHSCOPE_API_KEY is not set."""
        monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
        with pytest.raises(OSError, match="DASHSCOPE_API_KEY"):
            EmbeddingConfig()

    def test_loads_from_env(self, mock_env_dashscope: None) -> None:
        """Should load API key from environment with default base URL."""
        config = EmbeddingConfig()
        assert config.api_key == "sk-dashscope-test-key"
        assert "dashscope.aliyuncs.com" in config.base_url
        assert config.model == "text-embedding-v4"


class TestPipelineConfig:
    """Tests for PipelineConfig dataclass."""

    def test_default_values(self) -> None:
        """Should have sensible default values."""
        cfg = PipelineConfig()
        assert cfg.model_size == "tiny"
        assert cfg.batch_size == 10
        assert cfg.video_cooldown == 0
        assert cfg.asr_backend == "cpu"
        assert cfg.profile == "auto"
        assert cfg.continuous is False

    def test_custom_values(self) -> None:
        """Should accept custom values for all fields."""
        cfg = PipelineConfig(
            input_dir="/test/input",
            vault_dir="/test/vault",
            db_path="/test/db.sqlite",
            model_size="large-v3",
            cpu_threads=8,
            once=True,
            limit=50,
            batch_size=20,
            interval_seconds=3600,
            classify_only=True,
            refine=True,
            auto_refine_after=10,
            auto_refine_every_hours=24,
            retry_failed=True,
            semi=True,
            review_drafts=True,
            review_classifications=True,
            priority_level="idle",
            video_cooldown=30,
            embedding_enabled=True,
            parallel_workers=3,
            asr_backend="vulkan",
            profile="idle",
            continuous=True,
        )
        assert cfg.input_dir == "/test/input"
        assert cfg.vault_dir == "/test/vault"
        assert cfg.model_size == "large-v3"
        assert cfg.parallel_workers == 3
        assert cfg.asr_backend == "vulkan"
        assert cfg.profile == "idle"
        assert cfg.continuous is True


def test_hf_env_vars_set() -> None:
    """Verify HF environment variables are set on calling setup_environment()."""
    from src.models.config import setup_environment
    setup_environment()
    assert "HF_HOME" in os.environ
    assert "HF_HUB_CACHE" in os.environ
