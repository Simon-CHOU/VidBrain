"""Tests for audit logger."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.utils.audit import AuditLogger, get_audit
from src.utils.db import DatabaseManager


@pytest.fixture(autouse=True)
def reset_audit() -> None:
    AuditLogger._instance = None
    yield
    AuditLogger._instance = None


class TestAuditLogger:
    def test_singleton(self) -> None:
        assert get_audit() is AuditLogger.get()

    def test_log_writes_jsonl(self, tmp_path: Path) -> None:
        audit = AuditLogger.get()
        log_dir = tmp_path / "logs"
        audit.setup(str(log_dir))
        audit.log("test_event", "component", {"foo": "bar"})
        lines = (log_dir / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["event_type"] == "test_event"
        assert entry["details"]["foo"] == "bar"

    def test_log_with_db(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "audit.db")
        db = DatabaseManager(db_path)
        db.init_db()
        audit = AuditLogger.get()
        audit.setup(str(tmp_path / "logs"), db=db)
        audit.task_status_change("v1", "video.mp4", "PENDING", "ASR_PROCESSING")
        rows = db.query_audit_log(event_type="task_status_change", limit=5)
        assert len(rows) == 1

    def test_api_call_and_file_write(self, tmp_path: Path) -> None:
        audit = AuditLogger.get()
        audit.setup(str(tmp_path / "logs"))
        audit.api_call("deepseek", "chat", 120.5, success=True)
        audit.api_call("deepseek", "chat", 50.0, success=False, error="timeout")
        audit.file_write("/vault/note.md", 1024, "video.mp4")
        audit.system_event("startup", {"mode": "once"})
        audit.classification("id1", "v.mp4", "tech", "Python keyword")
        audit.queue_backpressure(25, 20)
        audit.error("pipeline", "boom", video_id="id1", video_name="v.mp4")
        assert audit.dump_json(str(tmp_path / "audit_export.json")) >= 6

    def test_dump_json_empty(self, tmp_path: Path) -> None:
        audit = AuditLogger.get()
        assert audit.dump_json(str(tmp_path / "empty.json")) == 0

    def test_dump_json_with_limit(self, tmp_path: Path) -> None:
        audit = AuditLogger.get()
        audit.setup(str(tmp_path / "logs"))
        for i in range(5):
            audit.log("evt", "c", {"i": i})
        count = audit.dump_json(str(tmp_path / "limited.json"), lines_limit=2)
        assert count == 2
