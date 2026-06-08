"""
Watchdog 文件监听模块。

监听本地目录中的 .mp4 文件写入事件（递归子目录），分类后异步提交处理。

重要约束：程序永远不得修改 input_dir 下的任何文件。
"""

from __future__ import annotations

import hashlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from src.models.config import LLMConfig, PipelineConfig
from src.services.asr_service import ASREngine
from src.services.classifier_service import classify_video
from src.services.pipeline_service import process_pipeline
from src.utils.audit import get_audit
from src.utils.db import DatabaseManager
from src.utils.metrics import get_metrics

logger = logging.getLogger("vidbrain.watcher")

# ── Watchdog 速率限制常量 ──
_DEBOUNCE_SECONDS = 5.0  # 同一文件的事件去抖窗口
_MIN_TASK_INTERVAL = 2.0  # 连续提交的最小间隔
_MAX_QUEUE_SIZE = 20  # 队列积压上限


class VideoFileHandler(FileSystemEventHandler):
    """文件系统事件处理器，捕捉 .mp4 文件的写入完成事件。"""

    def __init__(
        self,
        db: DatabaseManager,
        asr_engine: ASREngine,
        llm_config: LLMConfig,
        cfg: PipelineConfig,
        executor: ThreadPoolExecutor,
        input_dir: str,
    ) -> None:
        self._db = db
        self._asr_engine = asr_engine
        self._llm_config = llm_config
        self._cfg = cfg
        self._executor = executor
        self._input_dir = input_dir
        # 去抖与速率限制状态
        self._last_event_time: dict[str, float] = {}
        self._last_submit_time: float = 0.0

    def _should_throttle(self, file_path: str) -> bool:
        """检查是否应跳过当前事件（去抖 + 速率限制）。"""
        now = time.time()
        # 同一文件去抖
        last = self._last_event_time.get(file_path, 0)
        if now - last < _DEBOUNCE_SECONDS:
            return True
        self._last_event_time[file_path] = now
        # 连续提交最小间隔
        if now - self._last_submit_time < _MIN_TASK_INTERVAL:
            return True
        return False

    def _check_queue_backpressure(self) -> bool:
        """检查任务队列是否已积压过多。"""
        m = get_metrics()
        try:
            qsize = self._executor._work_queue.qsize()
        except Exception:
            return False
        m.set_gauge("queue_size", qsize)
        if qsize >= _MAX_QUEUE_SIZE:
            logger.warning(
                "[Watcher] 任务队列积压 %d 个 (上限 %d)，暂停接收新任务", qsize, _MAX_QUEUE_SIZE
            )
            get_audit().queue_backpressure(qsize, _MAX_QUEUE_SIZE)
            return True
        return False

    def on_closed(self, event) -> None:
        """文件写入完成并关闭句柄时触发。"""
        if event.is_directory or not event.src_path.lower().endswith(".mp4"):
            return

        # 去抖 + 连续提交间隔检查
        if self._should_throttle(event.src_path):
            return

        # 使用完整路径生成唯一 ID，避免不同子目录中同名文件冲突
        video_name = event.src_path.rsplit("\\", 1)[-1]
        video_id = hashlib.md5(event.src_path.encode("utf-8")).hexdigest()

        logger.info("[Watcher] 检测到新视频: %s (in %s)", video_name, event.src_path)
        self._db.create_task(video_id, video_name, event.src_path)

        # 分类
        cat, reason = classify_video(video_name)
        self._db.classify_task(video_id, cat, reason)
        get_audit().classification(video_id, video_name, cat, reason)
        logger.info("[Watcher] 分类: %s -> %s (%s)", video_name, cat, reason)

        # 仅处理 tech 类视频
        if cat != "tech":
            logger.info("[Watcher] 跳过非技术视频: %s", video_name)
            return

        # 队列积压保护
        if self._check_queue_backpressure():
            return

        # 异步提交到线程池，不阻塞监听器
        self._last_submit_time = time.time()
        self._executor.submit(
            process_pipeline,
            video_id,
            video_name,
            event.src_path,
            self._db,
            self._asr_engine,
            self._llm_config,
            self._cfg,
        )


def start_watcher(
    input_dir: str,
    db: DatabaseManager,
    asr_engine: ASREngine,
    llm_config: LLMConfig,
    cfg: PipelineConfig,
    executor: ThreadPoolExecutor,
) -> Observer:
    """启动 watchdog Observer，递归监听目录。"""
    event_handler = VideoFileHandler(db, asr_engine, llm_config, cfg, executor, input_dir)
    observer = Observer()
    observer.schedule(event_handler, path=input_dir, recursive=True)
    observer.start()
    logger.info("[Watcher] 开始监听目录（含子目录）: %s", input_dir)
    return observer
