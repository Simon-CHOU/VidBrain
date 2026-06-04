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
                        category TEXT,
                        classify_reason TEXT,
                        raw_asr_json TEXT,
                        error_message TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                # 兼容旧表：如果列不存在则添加
                for col in ["category", "classify_reason"]:
                    try:
                        conn.execute(f"ALTER TABLE video_pipeline ADD COLUMN {col} TEXT")
                    except sqlite3.OperationalError:
                        pass  # 列已存在
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

    def bulk_create_and_classify(self, items: list[tuple[str, str, str, str, str]]) -> None:
        """批量插入并分类视频。

        items: [(video_id, video_name, file_path, category, classify_reason), ...]
        """
        with self._lock:
            with sqlite3.connect(self._db_path) as conn:
                conn.executemany(
                    "INSERT OR IGNORE INTO video_pipeline (id, video_name, file_path, category, classify_reason) VALUES (?, ?, ?, ?, ?)",
                    items,
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

    def classify_task(self, video_id: str, category: str, reason: str) -> None:
        """更新视频分类结果。"""
        with self._lock:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    "UPDATE video_pipeline SET category=?, classify_reason=? WHERE id=?",
                    (category, reason, video_id),
                )
                conn.commit()

    def bulk_update_classification(self, items: list[tuple[str, str, str]]) -> None:
        """批量更新分类。

        items: [(category, classify_reason, video_id), ...]
        """
        with self._lock:
            with sqlite3.connect(self._db_path) as conn:
                conn.executemany(
                    "UPDATE video_pipeline SET category=?, classify_reason=? WHERE id=?",
                    items,
                )
                conn.commit()

    def get_pending_tech_tasks(self, limit: int = 5) -> list[dict]:
        """获取待处理的 tech 类视频，最多 limit 个。"""
        with self._lock:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM video_pipeline WHERE status='PENDING' AND category='tech' ORDER BY created_at ASC LIMIT ?",
                    (limit,),
                ).fetchall()
                return [dict(r) for r in rows]

    def count_unclassified(self) -> int:
        """获取未分类的视频数量。"""
        with self._lock:
            with sqlite3.connect(self._db_path) as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM video_pipeline WHERE category IS NULL"
                ).fetchone()
                return row[0] if row else 0

    def count_by_category(self) -> dict[str, int]:
        """按 category 统计视频数量。"""
        with self._lock:
            with sqlite3.connect(self._db_path) as conn:
                rows = conn.execute(
                    "SELECT category, COUNT(*) AS cnt FROM video_pipeline WHERE category IS NOT NULL GROUP BY category"
                ).fetchall()
                return {r[0]: r[1] for r in rows}

    def get_all_file_paths(self) -> set[str]:
        """获取所有已入库的文件路径集合。"""
        with self._lock:
            with sqlite3.connect(self._db_path) as conn:
                rows = conn.execute("SELECT file_path FROM video_pipeline").fetchall()
                return {r[0] for r in rows if r[0]}

    def get_uncategorized_ids(self) -> list[tuple[str, str, str]]:
        """获取所有未分类任务的 (id, video_name, file_path)。"""
        with self._lock:
            with sqlite3.connect(self._db_path) as conn:
                rows = conn.execute(
                    "SELECT id, video_name, file_path FROM video_pipeline WHERE category IS NULL"
                ).fetchall()
                return [(r[0], r[1], r[2]) for r in rows]
