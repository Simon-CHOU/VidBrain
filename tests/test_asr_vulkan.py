"""Tests for Vulkan ASR engine."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.services.asr_vulkan_service import (
    ASREngineVulkan,
    _find_ggml_model,
    _find_whisper_cli,
    _extract_audio,
)


class TestVulkanHelpers:
    def test_find_whisper_cli_from_env(self, tmp_path: Path, monkeypatch) -> None:
        cli = tmp_path / "whisper-cli.exe"
        cli.write_bytes(b"")
        monkeypatch.setenv("WHISPER_CLI_PATH", str(cli))
        assert _find_whisper_cli() == str(cli)

    def test_find_whisper_cli_missing(self, monkeypatch) -> None:
        monkeypatch.delenv("WHISPER_CLI_PATH", raising=False)
        with patch("shutil.which", return_value=None):
            assert _find_whisper_cli() is None

    def test_find_ggml_model(self, tmp_path: Path) -> None:
        model_dir = tmp_path / "models"
        model_dir.mkdir()
        (model_dir / "ggml-tiny.bin").write_bytes(b"model")
        found = _find_ggml_model("tiny", str(model_dir))
        assert found == str(model_dir / "ggml-tiny.bin")

    def test_find_ggml_model_unknown_size(self) -> None:
        assert _find_ggml_model("unknown") is None

    @patch("subprocess.run")
    def test_extract_audio_success(self, mock_run, tmp_path: Path) -> None:
        video = tmp_path / "v.mp4"
        video.write_bytes(b"fake")
        out = tmp_path / "out.wav"
        out.write_bytes(b"wav")

        def fake_run(cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b"wav")
            return MagicMock(returncode=0)

        mock_run.side_effect = fake_run
        result = _extract_audio(str(video), str(out))
        assert result == str(out)


class TestASREngineVulkan:
    @patch("src.services.asr_vulkan_service._find_ggml_model", return_value="/m/ggml-tiny.bin")
    @patch("src.services.asr_vulkan_service._find_whisper_cli", return_value="/cli")
    def test_vulkan_available_true(self, _cli, _model) -> None:
        engine = ASREngineVulkan(model_size="tiny", cpu_threads=2)
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            assert engine.vulkan_available is True

    def test_vulkan_unavailable_without_cli(self) -> None:
        with (
            patch("src.services.asr_vulkan_service._find_whisper_cli", return_value=None),
            patch("src.services.asr_vulkan_service._find_ggml_model", return_value=None),
        ):
            engine = ASREngineVulkan(model_size="tiny")
            assert engine.vulkan_available is False

    @patch("src.services.asr_vulkan_service._find_ggml_model", return_value="/m/model.bin")
    @patch("src.services.asr_vulkan_service._find_whisper_cli", return_value="/cli")
    def test_transcribe_cpu_fallback(self, _cli, _model) -> None:
        engine = ASREngineVulkan(model_size="tiny")
        engine._vulkan_available = False
        fallback = MagicMock()
        fallback.transcribe.return_value = [{"start": 0, "end": 1, "text": "hi"}]
        engine._cpu_fallback = fallback
        result = engine._transcribe_cpu_fallback("/v.mp4")
        assert result[0]["text"] == "hi"

    @patch("src.services.asr_vulkan_service._extract_audio", return_value="/tmp/a.wav")
    @patch("src.services.asr_vulkan_service._find_ggml_model", return_value="/m/model.bin")
    @patch("src.services.asr_vulkan_service._find_whisper_cli", return_value="/cli")
    def test_transcribe_vulkan_json(self, _cli, _model, _audio, tmp_path: Path) -> None:
        engine = ASREngineVulkan(model_size="tiny")
        engine._vulkan_available = True
        json_out = tmp_path / "a.json"
        json_out.write_text(
            json.dumps({"transcription": [{"timestamps": {"from": 0, "to": 1}, "text": "hello"}]}),
            encoding="utf-8",
        )
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            with patch.object(engine, "_transcribe_vulkan") as mock_v:
                mock_v.return_value = [{"start": 0.0, "end": 1.0, "text": "hello"}]
                result = engine.transcribe("/v.mp4")
        assert result[0]["text"] == "hello"
