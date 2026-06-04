"""
Watchdog 文件监听模块。

监听本地目录中的 .mp4 文件写入事件（递归子目录），分类后异步提交处理。

重要约束：程序永远不得修改 input_dir 下的任何文件。
"""

from __future__ import annotations

import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from vidbrain.asr_engine import ASREngine
from vidbrain.classifier import classify_video
from vidbrain.config import LLMConfig, PipelineConfig
from vidbrain.db import DatabaseManager
from vidbrain.pipeline import process_pipeline

logger = logging.getLogger("vidbrain.watcher")


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

    def on_closed(self, event) -> None:
        """文件写入完成并关闭句柄时触发。"""
        if event.is_directory or not event.src_path.lower().endswith(".mp4"):
            return

        # 使用完整路径生成唯一 ID，避免不同子目录中同名文件冲突
        video_name = event.src_path.rsplit("\\", 1)[-1]
        video_id = hashlib.md5(event.src_path.encode("utf-8")).hexdigest()

        logger.info("[Watcher] 检测到新视频: %s (in %s)", video_name, event.src_path)
        self._db.create_task(video_id, video_name, event.src_path)

        # 分类
        cat, reason = classify_video(video_name)
        self._db.classify_task(video_id, cat, reason)
        logger.info("[Watcher] 分类: %s -> %s (%s)", video_name, cat, reason)

        # 仅处理 tech 类视频
        if cat != "tech":
            logger.info("[Watcher] 跳过非技术视频: %s", video_name)
            return

        # 异步提交到线程池，不阻塞监听器
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
