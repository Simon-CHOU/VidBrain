"""Extended database manager tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from src.utils.db import DatabaseManager


@pytest.fixture
def db(tmp_path: Path) -> DatabaseManager:
    manager = DatabaseManager(str(tmp_path / "ext.db"))
    manager.init_db()
    return manager


class TestDatabaseManagerExtended:
    def test_update_status_with_asr_and_error(self, db: DatabaseManager) -> None:
        db.create_task("v1", "a.mp4", "/p/a.mp4")
        db.update_status("v1", "ASR_DONE", raw_asr='[{"text":"hi"}]')
        task = db.get_task("v1")
        assert task["status"] == "ASR_DONE"
        db.update_status("v1", "FAILED", error_msg="boom")
        task = db.get_task("v1")
        assert task["error_message"] == "boom"

    def test_get_pending_tasks(self, db: DatabaseManager) -> None:
        db.create_task("v1", "a.mp4", "/p/a.mp4")
        db.create_task("v2", "b.mp4", "/p/b.mp4")
        db.update_status("v2", "SUCCESS")
        pending = db.get_pending_tasks()
        assert len(pending) == 1
        assert pending[0]["id"] == "v1"

    def test_bulk_update_classification(self, db: DatabaseManager) -> None:
        db.create_task("v1", "a.mp4", "/p/a.mp4")
        db.bulk_update_classification([("tech", "reason", "v1")])
        task = db.get_task("v1")
        assert task["category"] == "tech"

    def test_count_unclassified(self, db: DatabaseManager) -> None:
        db.create_task("v1", "a.mp4", "/p/a.mp4")
        assert db.count_unclassified() == 1
        db.classify_task("v1", "tech", "x")
        assert db.count_unclassified() == 0

    def test_get_uncategorized_ids(self, db: DatabaseManager) -> None:
        db.create_task("v1", "a.mp4", "/p/a.mp4")
        rows = db.get_uncategorized_ids()
        assert rows == [("v1", "a.mp4", "/p/a.mp4")]

    def test_update_status_by_name(self, db: DatabaseManager) -> None:
        db.create_task("v1", "myvideo.mp4", "/p/myvideo.mp4")
        ok = db.update_status_by_name("myvideo", "SUCCESS")
        assert ok is True
        task = db.get_task("v1")
        assert task["status"] == "SUCCESS"

    def test_update_status_by_name_not_found(self, db: DatabaseManager) -> None:
        assert db.update_status_by_name("missing", "SUCCESS") is False

    def test_insert_and_query_audit_log(self, db: DatabaseManager) -> None:
        db.insert_audit_log(
            "test",
            "pipeline",
            "success",
            "vid1",
            "v.mp4",
            {"detail": 1},
        )
        rows = db.query_audit_log(event_type="test", video_id="vid1", limit=10)
        assert len(rows) == 1
        assert rows[0]["details"]["detail"] == 1

    def test_metrics_snapshots(self, db: DatabaseManager) -> None:
        snap = {
            "timestamp": "2025-01-01T00:00:00+00:00",
            "counters": {"total": 1},
        }
        db.insert_metrics_snapshot(snap)
        rows = db.get_recent_metrics_snapshots(limit=1)
        assert rows[0]["snapshot"]["counters"]["total"] == 1

    def test_get_task_missing(self, db: DatabaseManager) -> None:
        assert db.get_task("missing") is None
