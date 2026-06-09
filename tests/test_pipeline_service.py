"""Tests for pipeline service."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from src.models.config import LLMConfig, PipelineConfig
from src.services.pipeline_service import (
    _read_note_quality,
    process_pipeline,
)
from src.utils.db import DatabaseManager


class TestPipelineHelpers:
    def test_read_note_quality(self, tmp_path: Path) -> None:
        note = tmp_path / "note.md"
        note.write_text("---\nquality_score: 7\n---\n", encoding="utf-8")
        assert _read_note_quality(tmp_path, "note") == 7
        assert _read_note_quality(tmp_path, "missing") == 0


class TestProcessPipeline:
    @pytest.fixture
    def pipeline_ctx(self, tmp_path: Path, mock_env_deepseek) -> dict:
        vault = tmp_path / "vault"
        vault.mkdir()
        db_path = str(tmp_path / "pipe.db")
        db = DatabaseManager(db_path)
        db.init_db()
        cfg = PipelineConfig(
            input_dir=str(tmp_path / "input"),
            vault_dir=str(vault),
            db_path=db_path,
            model_size="tiny",
            cpu_threads=2,
            batch_size=1,
        )
        video_id = "vid1"
        video_name = "Python教程.mp4"
        file_path = "/fake/Python教程.mp4"
        db.create_task(video_id, video_name, file_path)
        db.classify_task(video_id, "tech", "Python")
        return {
            "db": db,
            "cfg": cfg,
            "llm": LLMConfig(),
            "video_id": video_id,
            "video_name": video_name,
            "file_path": file_path,
        }

    @patch("src.services.pipeline_service.check_and_update", return_value=0)
    @patch("src.services.pipeline_service.create_agent_graph")
    @patch("src.services.pipeline_service.check_related_notes", return_value=[])
    def test_success_auto_mode(
        self, mock_related, mock_graph, mock_update, pipeline_ctx: dict
    ) -> None:
        asr_engine = MagicMock()
        asr_engine.transcribe.return_value = [
            {"start": 0.0, "end": 1.0, "text": "hello"},
            {"start": 1.0, "end": 2.0, "text": "world"},
        ]
        mock_graph.return_value.invoke.return_value = {
            "final_markdown": "## Title\n\n[[Python]] [[CUDA]] [[ML]]",
            "update_suggestions": [],
        }
        process_pipeline(
            pipeline_ctx["video_id"],
            pipeline_ctx["video_name"],
            pipeline_ctx["file_path"],
            pipeline_ctx["db"],
            asr_engine,
            pipeline_ctx["llm"],
            pipeline_ctx["cfg"],
        )
        task = pipeline_ctx["db"].get_task(pipeline_ctx["video_id"])
        assert task["status"] == "SUCCESS"
        assert (Path(pipeline_ctx["cfg"].vault_dir) / "Python教程.md").is_file()

    @patch("src.services.pipeline_service.create_agent_graph")
    @patch("src.services.pipeline_service.check_related_notes", return_value=[])
    def test_semi_mode_writes_draft(self, mock_related, mock_graph, pipeline_ctx: dict) -> None:
        pipeline_ctx["cfg"].semi = True
        asr_engine = MagicMock()
        asr_engine.transcribe.return_value = [{"start": 0.0, "end": 1.0, "text": "x"}]
        mock_graph.return_value.invoke.return_value = {
            "final_markdown": "## Draft content",
            "update_suggestions": [],
        }
        process_pipeline(
            pipeline_ctx["video_id"],
            pipeline_ctx["video_name"],
            pipeline_ctx["file_path"],
            pipeline_ctx["db"],
            asr_engine,
            pipeline_ctx["llm"],
            pipeline_ctx["cfg"],
        )
        task = pipeline_ctx["db"].get_task(pipeline_ctx["video_id"])
        assert task["status"] == "DRAFT_PENDING"
        drafts_dir = Path(pipeline_ctx["cfg"].vault_dir) / "_drafts"
        assert any(drafts_dir.glob("*.md"))

    @patch("src.services.pipeline_service.create_agent_graph")
    @patch("src.services.pipeline_service.check_related_notes", return_value=[])
    def test_failure_retries_then_pending(
        self, mock_related, mock_graph, pipeline_ctx: dict
    ) -> None:
        asr_engine = MagicMock()
        asr_engine.transcribe.side_effect = RuntimeError("asr failed")
        process_pipeline(
            pipeline_ctx["video_id"],
            pipeline_ctx["video_name"],
            pipeline_ctx["file_path"],
            pipeline_ctx["db"],
            asr_engine,
            pipeline_ctx["llm"],
            pipeline_ctx["cfg"],
        )
        task = pipeline_ctx["db"].get_task(pipeline_ctx["video_id"])
        assert task["status"] == "PENDING"
        assert task["retry_count"] == 1

    @patch("src.services.pipeline_service.create_agent_graph")
    @patch("src.services.pipeline_service.check_related_notes", return_value=[])
    def test_permanent_failure_after_retries(
        self, mock_related, mock_graph, pipeline_ctx: dict
    ) -> None:
        asr_engine = MagicMock()
        asr_engine.transcribe.side_effect = RuntimeError("asr failed")
        db = pipeline_ctx["db"]
        vid = pipeline_ctx["video_id"]
        for _ in range(3):
            process_pipeline(
                vid,
                pipeline_ctx["video_name"],
                pipeline_ctx["file_path"],
                db,
                asr_engine,
                pipeline_ctx["llm"],
                pipeline_ctx["cfg"],
            )
        task = db.get_task(vid)
        assert task["status"] == "PERMANENTLY_FAILED"
