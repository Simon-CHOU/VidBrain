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
                # 启用 WAL 模式（写不阻塞读）+ 降低同步级别
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
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
                        retry_count INTEGER DEFAULT 0,
                        last_error TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                # 兼容旧表：仅在列不存在时才 ALTER TABLE
                existing_cols = {
                    r[1] for r in conn.execute("PRAGMA table_info(video_pipeline)").fetchall()
                }
                for col, col_type in [
                    ("category", "TEXT"),
                    ("classify_reason", "TEXT"),
                    ("retry_count", "INTEGER DEFAULT 0"),
                    ("last_error", "TEXT"),
                ]:
                    if col not in existing_cols:
                        conn.execute(f"ALTER TABLE video_pipeline ADD COLUMN {col} {col_type}")

                # ── Metrics 快照表 ──
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS metrics_snapshots (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        snapshot_json TEXT NOT NULL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # ── 审计日志表 ──
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS audit_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        component TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'success',
                        video_id TEXT DEFAULT '',
                        video_name TEXT DEFAULT '',
                        details_json TEXT DEFAULT '{}',
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_audit_event_type ON audit_log(event_type)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_audit_video_id ON audit_log(video_id)
                """)

                conn.commit()

    def _get_conn(self) -> sqlite3.Connection:
        """获取持久化连接（延迟创建，配合 check_same_thread=False）。"""
        if not hasattr(self, "_conn") or self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        return self._conn

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

    def increment_retry(self, video_id: str, error_msg: str) -> int:
        """失败时自增重试计数，返回新的重试次数。若已达上限则返回 -1。"""
        with self._lock:
            with sqlite3.connect(self._db_path) as conn:
                row = conn.execute(
                    "SELECT COALESCE(retry_count, 0) AS cnt FROM video_pipeline WHERE id=?",
                    (video_id,),
                ).fetchone()
                current = row[0] if row else 0
                new_count = current + 1
                conn.execute(
                    "UPDATE video_pipeline SET retry_count=?, last_error=? WHERE id=?",
                    (new_count, error_msg, video_id),
                )
                conn.commit()
                return new_count

    def reset_retry(self, video_id: str) -> None:
        """手动重置重试计数，将 FAILED 任务恢复为 PENDING 以便重新处理。"""
        with self._lock:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    "UPDATE video_pipeline SET status='PENDING', retry_count=0, last_error=NULL WHERE id=?",
                    (video_id,),
                )
                conn.commit()

    def recover_stuck_tasks(self) -> int:
        """将卡在中间状态的任务恢复为 PENDING，实现断点续运行。

        异常退出后，任务可能卡在 ASR_PROCESSING 或 AGENT_PROCESSING。
        此方法在启动时调用，将它们重置为 PENDING 以便重新处理。

        注意：AGENT_DONE 状态的任务已完成 LLM 处理，仅需重试写入步骤，
        不会被重置（避免浪费 API Token 重新生成已完成的 Agent 输出）。

        Returns:
            被恢复的任务数量。
        """
        with self._lock:
            with sqlite3.connect(self._db_path) as conn:
                cursor = conn.execute(
                    "UPDATE video_pipeline SET status='PENDING' "
                    "WHERE status IN ('ASR_PROCESSING', 'AGENT_PROCESSING')"
                )
                conn.commit()
                return cursor.rowcount

    def update_status_by_name(self, video_stem: str, status: str) -> bool:
        """通过视频文件名 stem 匹配更新状态（用于草稿发布/删除回调）。

        Returns:
            是否找到并更新了记录
        """
        with self._lock:
            with sqlite3.connect(self._db_path) as conn:
                cursor = conn.execute(
                    "UPDATE video_pipeline SET status=? WHERE video_name LIKE ?",
                    (status, f"{video_stem}.%"),
                )
                conn.commit()
                return cursor.rowcount > 0

    # ── Metrics / Audit 方法 ──

    def insert_metrics_snapshot(self, snapshot: dict) -> None:
        """写入一条指标快照。"""
        import json

        with self._lock:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    "INSERT INTO metrics_snapshots (timestamp, snapshot_json) VALUES (?, ?)",
                    (snapshot["timestamp"], json.dumps(snapshot, ensure_ascii=False)),
                )
                conn.commit()

    def get_recent_metrics_snapshots(self, limit: int = 10) -> list[dict]:
        """获取最近 N 条指标快照。"""
        import json

        with self._lock:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM metrics_snapshots ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                results = []
                for r in rows:
                    d = dict(r)
                    try:
                        d["snapshot"] = json.loads(d.pop("snapshot_json"))
                    except (json.JSONDecodeError, KeyError):
                        d["snapshot"] = {}
                    results.append(d)
                return results

    def insert_audit_log(
        self,
        event_type: str,
        component: str,
        status: str,
        video_id: str,
        video_name: str,
        details: dict,
    ) -> None:
        """写入一条审计日志。"""
        import json

        with self._lock:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    "INSERT INTO audit_log (timestamp, event_type, component, status, video_id, video_name, details_json) "
                    "VALUES (datetime('now'), ?, ?, ?, ?, ?, ?)",
                    (
                        event_type,
                        component,
                        status,
                        video_id,
                        video_name,
                        json.dumps(details, ensure_ascii=False),
                    ),
                )
                conn.commit()

    def query_audit_log(
        self,
        event_type: str = "",
        video_id: str = "",
        limit: int = 50,
    ) -> list[dict]:
        """查询审计日志，支持按事件类型和视频 ID 过滤。"""
        import json

        with self._lock:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                conditions = []
                params: list = []
                if event_type:
                    conditions.append("event_type = ?")
                    params.append(event_type)
                if video_id:
                    conditions.append("video_id = ?")
                    params.append(video_id)
                where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
                query = f"SELECT * FROM audit_log{where} ORDER BY id DESC LIMIT ?"
                params.append(limit)
                rows = conn.execute(query, params).fetchall()
                results = []
                for r in rows:
                    d = dict(r)
                    try:
                        d["details"] = json.loads(d.pop("details_json"))
                    except (json.JSONDecodeError, KeyError):
                        d["details"] = {}
                    results.append(d)
                return results

    def get_pipeline_stats(self) -> dict:
        """获取管线统计摘要（用于快速查看）。"""
        with self._lock:
            with sqlite3.connect(self._db_path) as conn:
                total = conn.execute("SELECT COUNT(*) FROM video_pipeline").fetchone()[0]
                by_status = conn.execute(
                    "SELECT status, COUNT(*) FROM video_pipeline GROUP BY status"
                ).fetchall()
                by_category = conn.execute(
                    "SELECT category, COUNT(*) FROM video_pipeline GROUP BY category"
                ).fetchall()
                return {
                    "total": total,
                    "by_status": dict(by_status),
                    "by_category": dict(by_category),
                }
