"""Tests for file watcher."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.models.config import LLMConfig, PipelineConfig
from src.utils.db import DatabaseManager
from src.utils.watcher import VideoFileHandler, start_watcher


@pytest.fixture
def watcher_setup(tmp_path: Path, mock_env_deepseek) -> dict:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    vault = tmp_path / "vault"
    vault.mkdir()
    db_path = str(tmp_path / "db.sqlite")
    db = DatabaseManager(db_path)
    db.init_db()
    cfg = PipelineConfig(
        input_dir=str(input_dir),
        vault_dir=str(vault),
        db_path=db_path,
        model_size="tiny",
        cpu_threads=2,
        batch_size=1,
    )
    llm = LLMConfig()
    executor = ThreadPoolExecutor(max_workers=1)
    asr_engine = MagicMock()
    return {
        "db": db,
        "cfg": cfg,
        "llm": llm,
        "executor": executor,
        "asr_engine": asr_engine,
        "input_dir": str(input_dir),
    }


class TestVideoFileHandler:
    def test_on_closed_ignores_non_mp4(self, watcher_setup: dict) -> None:
        handler = VideoFileHandler(
            watcher_setup["db"],
            watcher_setup["asr_engine"],
            watcher_setup["llm"],
            watcher_setup["cfg"],
            watcher_setup["executor"],
            watcher_setup["input_dir"],
        )
        event = MagicMock()
        event.is_directory = False
        event.src_path = "/path/file.txt"
        handler.on_closed(event)
        assert watcher_setup["db"].get_pipeline_stats()["total"] == 0

    def test_on_closed_ignores_directory(self, watcher_setup: dict) -> None:
        handler = VideoFileHandler(
            watcher_setup["db"],
            watcher_setup["asr_engine"],
            watcher_setup["llm"],
            watcher_setup["cfg"],
            watcher_setup["executor"],
            watcher_setup["input_dir"],
        )
        event = MagicMock()
        event.is_directory = True
        event.src_path = "/path/dir"
        handler.on_closed(event)
        assert watcher_setup["db"].get_pipeline_stats()["total"] == 0

    def test_on_closed_skips_non_tech(self, watcher_setup: dict) -> None:
        handler = VideoFileHandler(
            watcher_setup["db"],
            watcher_setup["asr_engine"],
            watcher_setup["llm"],
            watcher_setup["cfg"],
            watcher_setup["executor"],
            watcher_setup["input_dir"],
        )
        event = MagicMock()
        event.is_directory = False
        event.src_path = str(Path(watcher_setup["input_dir"]) / "娱乐视频.mp4")
        handler.on_closed(event)
        task = watcher_setup["db"].get_task(
            __import__("hashlib").md5(event.src_path.encode()).hexdigest()
        )
        assert task is not None
        assert task["category"] == "skip"

    def test_should_throttle_debounce(self, watcher_setup: dict) -> None:
        handler = VideoFileHandler(
            watcher_setup["db"],
            watcher_setup["asr_engine"],
            watcher_setup["llm"],
            watcher_setup["cfg"],
            watcher_setup["executor"],
            watcher_setup["input_dir"],
        )
        path = "/videos/test.mp4"
        handler._last_event_time[path] = __import__("time").time()
        assert handler._should_throttle(path) is True

    def test_queue_backpressure(self, watcher_setup: dict) -> None:
        handler = VideoFileHandler(
            watcher_setup["db"],
            watcher_setup["asr_engine"],
            watcher_setup["llm"],
            watcher_setup["cfg"],
            watcher_setup["executor"],
            watcher_setup["input_dir"],
        )
        handler._executor._work_queue = MagicMock()
        handler._executor._work_queue.qsize.return_value = 25
        assert handler._check_queue_backpressure() is True

    @patch("src.utils.watcher.process_pipeline")
    def test_on_closed_submits_tech_video(self, mock_pipeline, watcher_setup: dict) -> None:
        handler = VideoFileHandler(
            watcher_setup["db"],
            watcher_setup["asr_engine"],
            watcher_setup["llm"],
            watcher_setup["cfg"],
            watcher_setup["executor"],
            watcher_setup["input_dir"],
        )
        video_path = str(Path(watcher_setup["input_dir"]) / "Python教程.mp4")
        event = MagicMock()
        event.is_directory = False
        event.src_path = video_path
        handler.on_closed(event)
        mock_pipeline.assert_called_once()


class TestStartWatcher:
    @patch("src.utils.watcher.Observer")
    def test_start_watcher(self, mock_observer_cls, watcher_setup: dict) -> None:
        observer = MagicMock()
        mock_observer_cls.return_value = observer
        result = start_watcher(
            watcher_setup["input_dir"],
            watcher_setup["db"],
            watcher_setup["asr_engine"],
            watcher_setup["llm"],
            watcher_setup["cfg"],
            watcher_setup["executor"],
        )
        assert result is observer
        observer.schedule.assert_called_once()
        observer.start.assert_called_once()
