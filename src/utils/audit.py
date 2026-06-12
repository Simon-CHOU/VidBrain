"""
审计追踪模块。

记录所有关键操作的完整审计日志，包含：
- 任务状态变更（PENDING → ASR_PROCESSING → ... → SUCCESS/FAILED）
- API 调用（来源、目标、耗时、成功/失败）
- 文件写入操作（目标路径、大小）
- 系统事件（启动、关闭、异常）

双重输出：
1. 结构化 JSON Lines 文件: logs/audit.jsonl
2. SQLite audit_log 表（可查询）
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("vidbrain.audit")


class AuditLogger:
    """全局单例审计日志记录器，线程安全。"""

    _instance: Optional["AuditLogger"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._mu = threading.Lock()
        self._log_path: str = ""
        self._db: object = None  # DatabaseManager ref

    # ── 单例 ──

    @classmethod
    def get(cls) -> "AuditLogger":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── 初始化 ──

    def setup(self, log_dir: str = "logs", db: object = None) -> None:
        """初始化审计日志路径和数据库绑定。"""
        os.makedirs(log_dir, exist_ok=True)
        self._log_path = os.path.join(log_dir, "audit.jsonl")
        self._db = db
        logger.info("审计日志已启用: %s", self._log_path)

    # ── 核心记录方法 ──

    def log(
        self,
        event_type: str,
        component: str,
        details: dict[str, Any] | None = None,
        status: str = "success",
        video_id: str = "",
        video_name: str = "",
    ) -> None:
        """记录一条审计事件。"""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "component": component,
            "status": status,
            "video_id": video_id or (details or {}).get("video_id", ""),
            "video_name": video_name or (details or {}).get("video_name", ""),
            "details": details or {},
        }

        # 写入 JSON Lines 文件
        if self._log_path:
            try:
                with open(self._log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except Exception as e:
                logger.warning("审计日志写入文件失败: %s", str(e))

        # 写入 SQLite（如已绑定）
        db = self._db
        if db is not None:
            try:
                db.insert_audit_log(  # type: ignore[attr-defined]
                    event_type, component, status, video_id, video_name, entry["details"]
                )
            except Exception as e:
                logger.warning("审计日志写入 DB 失败: %s", str(e))

    # ── 便捷方法 ──

    def task_status_change(
        self,
        video_id: str,
        video_name: str,
        from_status: str,
        to_status: str,
        reason: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        """记录任务状态变更。"""
        d = details or {}
        d.update({"from_status": from_status, "to_status": to_status})
        if reason:
            d["reason"] = reason
        status = "failure" if to_status in ("FAILED", "PERMANENTLY_FAILED") else "success"
        self.log(
            "task_status_change",
            "pipeline",
            d,
            status=status,
            video_id=video_id,
            video_name=video_name,
        )
        # 同时输出简洁日志
        emoji = "✓" if status == "success" else "✗"
        logger.info("[Audit] %s %s: %s → %s", emoji, video_name, from_status, to_status)

    def api_call(
        self,
        provider: str,
        endpoint: str,
        duration_ms: float,
        success: bool,
        error: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        """记录 API 调用。"""
        d = details or {}
        d.update(
            {
                "provider": provider,
                "endpoint": endpoint,
                "duration_ms": round(duration_ms, 1),
            }
        )
        if error:
            d["error"] = error
        self.log("api_call", "llm", d, status="success" if success else "failure")

    def file_write(self, file_path: str, size_bytes: int, video_name: str = "") -> None:
        """记录文件写入操作。"""
        self.log(
            "file_write",
            "pipeline",
            {"file_path": file_path, "size_bytes": size_bytes},
            video_name=video_name,
        )

    def system_event(self, event: str, details: dict[str, Any] | None = None) -> None:
        """记录系统事件（启动、关闭、异常等）。"""
        self.log("system_event", "system", details or {"event": event})

    def classification(
        self,
        video_id: str,
        video_name: str,
        category: str,
        reason: str,
    ) -> None:
        """记录分类决策。"""
        self.log(
            "classification",
            "classifier",
            {"category": category, "reason": reason},
            video_id=video_id,
            video_name=video_name,
        )

    def queue_backpressure(self, queue_size: int, max_size: int) -> None:
        """记录队列积压事件。"""
        self.log(
            "queue_backpressure",
            "watcher",
            {"queue_size": queue_size, "max_size": max_size},
            status="warning",
        )

    def error(
        self,
        component: str,
        error_message: str,
        video_id: str = "",
        video_name: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        """记录错误事件。"""
        d = details or {}
        d["error_message"] = error_message
        self.log("error", component, d, status="failure", video_id=video_id, video_name=video_name)

    # ── 导出审计日志 ──

    def dump_json(self, filepath: str, lines_limit: int = 0) -> int:
        """导出审计日志为格式化 JSON 数组文件。

        Args:
            filepath: 输出路径
            lines_limit: 0=全部，>0=最近 N 条

        Returns:
            导出的记录数
        """
        if not self._log_path or not os.path.exists(self._log_path):
            return 0
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        entries: list[dict] = []
        with open(self._log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        if lines_limit > 0:
            entries = entries[-lines_limit:]
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
        logger.info("审计日志已导出 %d 条到: %s", len(entries), filepath)
        return len(entries)


# ── 便捷全局函数 ──


def get_audit() -> AuditLogger:
    return AuditLogger.get()
