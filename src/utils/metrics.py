"""
运行时指标收集模块。

提供全局单例 MetricsCollector，收集以下维度的指标：
- 处理吞吐量：总处理数、成功数、失败数、永久失败数
- 延迟分布：ASR 耗时、Agent 耗时、Pipeline 总耗时
- 队列状态：当前积压数、历史最高积压
- API 调用：LLM 总调用次数、失败次数
- 批次统计：已完成批次数、上次活跃时间
- 运行状态：进程启动时间、运行时长

指标定期写入 SQLite metrics_snapshots 表，并支持 JSON 导出。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("vidbrain.metrics")


class MetricsCollector:
    """全局单例指标收集器，线程安全。"""

    _instance: Optional["MetricsCollector"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._mu = threading.Lock()
        self._start_time = time.time()
        self._counters: dict[str, int] = defaultdict(int)
        self._gauges: dict[str, float] = defaultdict(float)
        self._durations: dict[str, list[float]] = defaultdict(list)
        self._event_times: dict[str, float] = {}
        self._db_path: str = ""
        self._db: object = None  # DatabaseManager ref, 延迟绑定

    # ── 单例 ──

    @classmethod
    def get(cls) -> "MetricsCollector":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── 绑定数据库 ──

    def bind_db(self, db: object, db_path: str) -> None:
        """绑定 DatabaseManager 实例，用于定期落盘。"""
        self._db = db
        self._db_path = db_path

    # ── Counter 类指标 ──

    def incr(self, name: str, delta: int = 1) -> None:
        with self._mu:
            self._counters[name] += delta

    # ── Gauge 类指标 ──

    def set_gauge(self, name: str, value: float) -> None:
        with self._mu:
            self._gauges[name] = value

    # ── Duration 记录 ──

    def record_duration(self, name: str, duration_seconds: float) -> None:
        with self._mu:
            self._durations[name].append(duration_seconds)
            # 只保留最近 1000 个样本
            if len(self._durations[name]) > 1000:
                self._durations[name] = self._durations[name][-1000:]

    # ── 事件标记 ──

    def mark_event(self, name: str) -> None:
        with self._mu:
            self._event_times[name] = time.time()

    # ── 快照生成 ──

    def snapshot(self) -> dict:
        """生成当前指标快照字典。"""
        with self._mu:
            now = time.time()
            uptime = now - self._start_time

            def _stats(durations: list[float]) -> dict:
                if not durations:
                    return {
                        "count": 0,
                        "avg": 0,
                        "p50": 0,
                        "p95": 0,
                        "p99": 0,
                        "max": 0,
                    }
                s = sorted(durations)
                n = len(s)
                return {
                    "count": n,
                    "avg": round(sum(s) / n, 3),
                    "p50": round(s[int(n * 0.50)], 3),
                    "p95": round(s[int(n * 0.95)], 3) if n >= 20 else round(s[-1], 3),
                    "p99": round(s[int(n * 0.99)], 3) if n >= 100 else round(s[-1], 3),
                    "max": round(s[-1], 3),
                }

            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "uptime_seconds": round(uptime, 1),
                "uptime_human": _format_duration(uptime),
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "durations": {k: _stats(v) for k, v in self._durations.items()},
            }

    def dump_json(self, filepath: str) -> None:
        """导出当前快照到 JSON 文件。"""
        snap = self.snapshot()
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=2)
        logger.info("指标已导出: %s", filepath)

    def flush_to_db(self) -> None:
        """将当前快照写入 SQLite（如已绑定）。"""
        db = self._db
        if db is None:
            return
        snap = self.snapshot()
        try:
            db.insert_metrics_snapshot(snap)  # type: ignore[attr-defined]
        except Exception as e:
            logger.warning("指标落盘失败: %s", str(e))

    # ── 重置 durations（导出后可选的清理） ──

    def reset_durations(self) -> None:
        with self._mu:
            self._durations.clear()

    # ── 摘要日志 ──

    def log_summary(self) -> None:
        """打印当前指标摘要到 logger。"""
        snap = self.snapshot()
        c = snap["counters"]
        lines = [
            "━━━ Metrics 摘要 ━━━",
            f"  运行时长: {snap['uptime_human']}",
            f"  总处理: {c.get('total_processed', 0)}",
            f"  成功: {c.get('total_succeeded', 0)}",
            f"  失败: {c.get('total_failed', 0)} (永久: {c.get('total_permanently_failed', 0)})",
            f"  LLM调用: {c.get('llm_calls_total', 0)} (失败: {c.get('llm_calls_failed', 0)})",
            f"  批次完成: {c.get('batches_completed', 0)}",
        ]
        for k, v in snap["durations"].items():
            if v["count"] > 0:
                lines.append(
                    f"  {k}: avg={v['avg']}s, p95={v['p95']}s, max={v['max']}s"
                )
        logger.info("\n".join(lines))


def _format_duration(seconds: float) -> str:
    """将秒数格式化为人类可读字符串。"""
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    if seconds < 86400:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}h {m}m"
    d = int(seconds // 86400)
    h = int((seconds % 86400) // 3600)
    return f"{d}d {h}h"


# ── 便捷全局函数 ──


def get_metrics() -> MetricsCollector:
    return MetricsCollector.get()
