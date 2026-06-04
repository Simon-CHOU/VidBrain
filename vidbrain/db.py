"""
SQLite 数据库操作。

线程安全封装，管理视频处理任务的状态机流转。
"""

from __future__ import annotations

import sqlite3
import threading
from typing import Optional


class DatabaseManager:
    """SQLite 数据库管理器，线程安全。"""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()

    def init_db(self) -> None:
        """初始化数据库表结构及触发器。"""
        with self._lock:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS video_pipeline (
                        id TEXT PRIMARY KEY,
                        video_name TEXT NOT NULL,
                        file_path TEXT NOT NULL,
                        status TEXT DEFAULT 'PENDING',
                        raw_asr_json TEXT,
                        error_message TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                # 自动更新 updated_at 的触发器
                conn.execute("""
                    CREATE TRIGGER IF NOT EXISTS trg_video_pipeline_updated_at
                    AFTER UPDATE ON video_pipeline
                    FOR EACH ROW
                    BEGIN
                        UPDATE video_pipeline SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
                    END;
                """)
                conn.commit()

    def create_task(self, video_id: str, video_name: str, file_path: str) -> None:
        """插入新任务（如已存在则忽略）。"""
        with self._lock:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO video_pipeline (id, video_name, file_path) VALUES (?, ?, ?)",
                    (video_id, video_name, file_path),
                )
                conn.commit()

    def update_status(
        self,
        video_id: str,
        status: str,
        raw_asr: Optional[str] = None,
        error_msg: Optional[str] = None,
    ) -> None:
        """更新任务状态，可选更新 ASR 结果或错误信息。"""
        with self._lock:
            with sqlite3.connect(self._db_path) as conn:
                if raw_asr is not None:
                    conn.execute(
                        "UPDATE video_pipeline SET status=?, raw_asr_json=? WHERE id=?",
                        (status, raw_asr, video_id),
                    )
                elif error_msg is not None:
                    conn.execute(
                        "UPDATE video_pipeline SET status=?, error_message=? WHERE id=?",
                        (status, error_msg, video_id),
                    )
                else:
                    conn.execute(
                        "UPDATE video_pipeline SET status=? WHERE id=?",
                        (status, video_id),
                    )
                conn.commit()

    def get_task(self, video_id: str) -> Optional[dict]:
        """查询单个任务。"""
        with self._lock:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM video_pipeline WHERE id=?", (video_id,)
                ).fetchone()
                return dict(row) if row else None

    def get_pending_tasks(self) -> list[dict]:
        """获取所有待处理任务（PENDING 状态）。"""
        with self._lock:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM video_pipeline WHERE status='PENDING'"
                ).fetchall()
                return [dict(r) for r in rows]
