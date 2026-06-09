"""Shared pytest fixtures for VidBrain tests."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Generator

import pytest

# Import from src modules
from src.models.config import PipelineConfig


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Create a temporary directory, yield it, and clean up."""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def mock_env_deepseek(monkeypatch) -> None:
    """Set mock DeepSeek environment variables."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-key-12345")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")


@pytest.fixture
def mock_env_dashscope(monkeypatch) -> None:
    """Set mock DashScope environment variables."""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-dashscope-test-key")


@pytest.fixture
def pipeline_config(temp_dir) -> PipelineConfig:
    """Create a test PipelineConfig."""
    return PipelineConfig(
        input_dir=str(temp_dir / "input"),
        vault_dir=str(temp_dir / "vault"),
        db_path=str(temp_dir / "test.db"),
        model_size="tiny",
        cpu_threads=2,
        batch_size=3,
    )


@pytest.fixture
def sample_markdown_content() -> str:
    """Sample markdown content with front-matter."""
    return (
        "---\n"
        "type: technical-note\n"
        "source_video: test_video.mp4\n"
        "status: auto-generated\n"
        "quality_score: 7\n"
        "created: 2025-01-15 10:30:00\n"
        "---\n\n"
        "## Introduction\n\n"
        "This is a test note about [[Python]] and [[Machine Learning]].\n\n"
        "## Details\n\n"
        "More content about `CUDA` and `GPU` optimization.\n"
    )


@pytest.fixture
def chunk_store_fixture(tmp_path):
    """Create a test ChunkStore with a pre-populated note."""
    from unittest.mock import MagicMock

    from src.services.chunk_service import ChunkStore

    store = ChunkStore(str(tmp_path))
    engine = MagicMock()
    engine.embed_batch.return_value = [[0.1] * 1024, [0.2] * 1024, [0.3] * 1024]
    engine.embed.return_value = [0.1] * 1024

    content = (
        "## CUDA Optimization\n"
        "CUDA kernel fusion reduces kernel launch overhead.\n"
        "It is a key technique for GPU performance.\n\n"
        "## Memory Management\n"
        "Proper memory management avoids fragmentation.\n"
        "Use pinned memory for faster transfers."
    )
    store.chunk_note("TestGPU", content, engine)
    return store, engine
