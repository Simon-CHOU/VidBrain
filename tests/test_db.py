"""Tests for database manager."""

from __future__ import annotations

from pathlib import Path

import pytest
from src.utils.db import DatabaseManager


class TestDatabaseManager:
    """Tests for DatabaseManager class."""

    @pytest.fixture
    def db(self, tmp_path: Path) -> DatabaseManager:
        """Create a fresh in-memory-like database manager."""
        db_path = str(tmp_path / "test.db")
        manager = DatabaseManager(db_path)
        manager.init_db()
        return manager

    def test_init_db_creates_tables(self, db: DatabaseManager) -> None:
        """Should create video_pipeline table."""
        stats = db.get_pipeline_stats()
        assert stats["total"] == 0

    def test_create_task(self, db: DatabaseManager) -> None:
        """Should insert a new task."""
        db.create_task("vid1", "test.mp4", "/path/to/test.mp4")
        task = db.get_task("vid1")
        assert task is not None
        assert task["video_name"] == "test.mp4"
        assert task["status"] == "PENDING"

    def test_create_task_idempotent(self, db: DatabaseManager) -> None:
        """Should ignore duplicate task creation."""
        db.create_task("vid1", "test.mp4", "/path/to/test.mp4")
        db.create_task("vid1", "test2.mp4", "/path/to/test2.mp4")
        task = db.get_task("vid1")
        assert task["video_name"] == "test.mp4"  # original preserved

    def test_update_status(self, db: DatabaseManager) -> None:
        """Should update task status."""
        db.create_task("vid1", "test.mp4", "/path/to/test.mp4")
        db.update_status("vid1", "ASR_PROCESSING")
        task = db.get_task("vid1")
        assert task["status"] == "ASR_PROCESSING"

    def test_classify_task(self, db: DatabaseManager) -> None:
        """Should update task classification."""
        db.create_task("vid1", "test.mp4", "/path/to/test.mp4")
        db.classify_task("vid1", "tech", "keyword match: Python")
        task = db.get_task("vid1")
        assert task["category"] == "tech"
        assert "Python" in task["classify_reason"]

    def test_bulk_create_and_classify(self, db: DatabaseManager) -> None:
        """Should bulk insert and classify tasks."""
        items = [
            ("vid1", "a.mp4", "/p/a.mp4", "tech", "Python"),
            ("vid2", "b.mp4", "/p/b.mp4", "skip", "娱乐"),
        ]
        db.bulk_create_and_classify(items)
        cats = db.count_by_category()
        assert cats.get("tech") == 1
        assert cats.get("skip") == 1

    def test_get_pending_tech_tasks(self, db: DatabaseManager) -> None:
        """Should return only tech tasks with PENDING status."""
        db.create_task("t1", "tech1.mp4", "/p/t1.mp4")
        db.classify_task("t1", "tech", "Python")
        db.create_task("t2", "tech2.mp4", "/p/t2.mp4")
        db.classify_task("t2", "tech", "Go")
        db.create_task("s1", "skip1.mp4", "/p/s1.mp4")
        db.classify_task("s1", "skip", "娱乐")

        tasks = db.get_pending_tech_tasks(limit=10)
        assert len(tasks) == 2
        for t in tasks:
            assert t["category"] == "tech"

    def test_increment_retry(self, db: DatabaseManager) -> None:
        """Should increment retry count on failure."""
        db.create_task("vid1", "test.mp4", "/p/test.mp4")
        count = db.increment_retry("vid1", "error occurred")
        assert count == 1
        count = db.increment_retry("vid1", "error again")
        assert count == 2

    def test_reset_retry(self, db: DatabaseManager) -> None:
        """Should reset retry count and status."""
        db.create_task("vid1", "test.mp4", "/p/test.mp4")
        db.increment_retry("vid1", "error")
        db.reset_retry("vid1")
        task = db.get_task("vid1")
        assert task["status"] == "PENDING"
        assert task["retry_count"] == 0

    def test_recover_stuck_tasks(self, db: DatabaseManager) -> None:
        """Should reset stuck tasks to PENDING."""
        db.create_task("vid1", "a.mp4", "/p/a.mp4")
        db.create_task("vid2", "b.mp4", "/p/b.mp4")
        db.update_status("vid1", "ASR_PROCESSING")
        db.update_status("vid2", "AGENT_PROCESSING")
        count = db.recover_stuck_tasks()
        assert count == 2
        for vid in ["vid1", "vid2"]:
            task = db.get_task(vid)
            assert task["status"] == "PENDING"

    def test_get_all_file_paths(self, db: DatabaseManager) -> None:
        """Should return set of all file paths."""
        db.create_task("v1", "a.mp4", "/p/a.mp4")
        db.create_task("v2", "b.mp4", "/p/b.mp4")
        paths = db.get_all_file_paths()
        assert paths == {"/p/a.mp4", "/p/b.mp4"}

    def test_count_by_category(self, db: DatabaseManager) -> None:
        """Should count tasks by category."""
        items = [
            ("v1", "a.mp4", "/p/a.mp4", "tech", ""),
            ("v2", "b.mp4", "/p/b.mp4", "tech", ""),
            ("v3", "c.mp4", "/p/c.mp4", "skip", ""),
            ("v4", "d.mp4", "/p/d.mp4", "unclear", ""),
        ]
        db.bulk_create_and_classify(items)
        cats = db.count_by_category()
        assert cats["tech"] == 2
        assert cats["skip"] == 1
        assert cats["unclear"] == 1

    def test_pipeline_stats(self, db: DatabaseManager) -> None:
        """Should return comprehensive pipeline stats."""
        db.create_task("v1", "a.mp4", "/p/a.mp4")
        db.update_status("v1", "SUCCESS")
        stats = db.get_pipeline_stats()
        assert stats["total"] == 1
        assert stats["by_status"]["SUCCESS"] == 1
