"""Tests for metrics collector."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from src.utils.metrics import MetricsCollector, _format_duration, get_metrics


@pytest.fixture(autouse=True)
def reset_metrics() -> None:
    MetricsCollector._instance = None
    yield
    MetricsCollector._instance = None


class TestFormatDuration:
    def test_seconds(self) -> None:
        assert _format_duration(45) == "45s"

    def test_minutes(self) -> None:
        assert _format_duration(125) == "2m 5s"

    def test_hours(self) -> None:
        assert "h" in _format_duration(3700)

    def test_days(self) -> None:
        assert "d" in _format_duration(90000)


class TestMetricsCollector:
    def test_singleton(self) -> None:
        a = MetricsCollector.get()
        b = get_metrics()
        assert a is b

    def test_counters_and_gauges(self) -> None:
        m = MetricsCollector.get()
        m.incr("total_processed", 2)
        m.set_gauge("queue_size", 5.0)
        snap = m.snapshot()
        assert snap["counters"]["total_processed"] == 2
        assert snap["gauges"]["queue_size"] == 5.0

    def test_record_duration_stats(self) -> None:
        m = MetricsCollector.get()
        for v in [1.0, 2.0, 3.0, 4.0, 5.0]:
            m.record_duration("asr_duration", v)
        stats = m.snapshot()["durations"]["asr_duration"]
        assert stats["count"] == 5
        assert stats["avg"] == 3.0

    def test_mark_event(self) -> None:
        m = MetricsCollector.get()
        m.mark_event("last_batch_time")
        assert "last_batch_time" in m._event_times

    def test_dump_json(self, tmp_path: Path) -> None:
        m = MetricsCollector.get()
        m.incr("batches_completed")
        out = tmp_path / "metrics.json"
        m.dump_json(str(out))
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["counters"]["batches_completed"] == 1

    def test_flush_to_db(self, tmp_path: Path) -> None:
        from src.utils.db import DatabaseManager

        db_path = str(tmp_path / "m.db")
        db = DatabaseManager(db_path)
        db.init_db()
        m = MetricsCollector.get()
        m.bind_db(db, db_path)
        m.incr("total_succeeded")
        m.flush_to_db()
        rows = db.get_recent_metrics_snapshots(limit=1)
        assert len(rows) == 1

    def test_flush_to_db_without_bind(self) -> None:
        MetricsCollector.get().flush_to_db()

    def test_reset_durations(self) -> None:
        m = MetricsCollector.get()
        m.record_duration("x", 1.0)
        m.reset_durations()
        assert m.snapshot()["durations"] == {}

    def test_log_summary(self) -> None:
        m = MetricsCollector.get()
        m.incr("total_processed")
        m.log_summary()
