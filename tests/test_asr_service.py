"""Tests for ASR engine."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from src.services.asr_service import (
    ASREngine,
    _find_cached_snapshot,
    _repo_to_cache_dir,
)


class TestAsrHelpers:
    def test_repo_to_cache_dir(self) -> None:
        assert (
            _repo_to_cache_dir("Systran/faster-whisper-tiny")
            == "Systran--faster-whisper-tiny"
        )

    def test_find_cached_snapshot_missing(self) -> None:
        assert _find_cached_snapshot("unknown-model") is None

    def test_find_cached_snapshot_with_cache(self, tmp_path: Path, monkeypatch) -> None:
        repo = "Systran/faster-whisper-tiny"
        snap_dir = (
            tmp_path / f"models--{_repo_to_cache_dir(repo)}" / "snapshots" / "abc123"
        )
        snap_dir.mkdir(parents=True)
        (snap_dir / "model.bin").write_bytes(b"x")
        (snap_dir / "config.json").write_text("{}")
        (snap_dir / "tokenizer.json").write_text("{}")
        monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
        found = _find_cached_snapshot("tiny")
        assert found == str(snap_dir)


class TestASREngine:
    @patch("src.services.asr_service.WhisperModel")
    def test_transcribe(self, mock_model_cls) -> None:
        segment = MagicMock()
        segment.text = "hello world"
        segment.start = 0.0
        segment.end = 1.0
        mock_model_cls.return_value.transcribe.return_value = ([segment], None)
        engine = ASREngine(model_size="tiny", cpu_threads=2)
        with patch.object(
            engine, "_load_model", return_value=mock_model_cls.return_value
        ):
            result = engine.transcribe("/fake/video.mp4")
        assert result[0]["text"] == "hello world"

    @patch("src.services.asr_service.WhisperModel")
    def test_prepare_model(self, mock_model_cls) -> None:
        engine = ASREngine(model_size="tiny", cpu_threads=2)
        with patch.object(
            engine, "_load_model", return_value=mock_model_cls.return_value
        ):
            engine.prepare_model("tiny", 2)
        mock_model_cls.assert_not_called()  # _load_model mocked

    @patch("src.services.asr_service._find_cached_snapshot", return_value=None)
    @patch("src.services.asr_service.WhisperModel")
    def test_load_model_download_path(self, mock_model_cls, _mock_cache) -> None:
        engine = ASREngine(model_size="tiny", cpu_threads=2)
        engine._load_model("tiny")
        mock_model_cls.assert_called()
