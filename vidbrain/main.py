"""
VidBrain 主入口。

CLI 参数解析 + 各模块组装 + 启动。

重要约束：程序永远不得修改 input_dir（即 I:/web-videos）下的任何文件。
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from vidbrain.asr_engine import ASREngine
from vidbrain.config import LLMConfig, PipelineConfig
from vidbrain.db import DatabaseManager
from vidbrain.logger import setup_logger
from vidbrain.pipeline import process_pipeline
from vidbrain.watcher import start_watcher

logger = logging.getLogger("vidbrain")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析 CLI 参数。"""
    parser = argparse.ArgumentParser(
        description="VidBrain - 本地视频 ASR 提取与云端 Agent 自迭代 Obsidian 知识库系统"
    )
    parser.add_argument(
        "--input-dir",
        default=r"I:\web-videos",
        help="输入目录（存放 .mp4 网络视频，默认: I:\\web-videos）",
    )
    parser.add_argument(
        "--vault-dir",
        required=True,
        help="Obsidian Vault 路径（必填）",
    )
    parser.add_argument(
        "--db-path",
        default="./pipeline.db",
        help="SQLite 数据库路径（默认: ./pipeline.db）",
    )
    parser.add_argument(
        "--model-size",
        default="large-v3",
        help="Whisper 模型大小（默认: large-v3）",
    )
    parser.add_argument(
        "--cpu-threads",
        type=int,
        default=0,
        help="ASR 使用的 CPU 线程数（默认: 逻辑核心数 - 1）",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="处理现有文件后退出（不持续监听）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="最多处理的视频数（0=不限制，默认: 0）",
    )
    return parser.parse_args(argv)


def build_config(args: argparse.Namespace) -> PipelineConfig:
    """将 CLI 参数转换为 PipelineConfig。"""
    import multiprocessing

    cpu_threads = args.cpu_threads if args.cpu_threads > 0 else max(
        1, multiprocessing.cpu_count() - 1
    )
    return PipelineConfig(
        input_dir=args.input_dir,
        vault_dir=args.vault_dir,
        db_path=args.db_path,
        model_size=args.model_size,
        cpu_threads=cpu_threads,
        once=args.once,
        limit=args.limit,
    )


def main(argv: list[str] | None = None) -> None:
    """主入口函数。"""
    args = parse_args(argv)
    cfg = build_config(args)

    # 初始化日志
    setup_logger()
    logger.info("VidBrain 启动")

    # 初始化 LLM 配置（会验证环境变量）
    try:
        llm_config = LLMConfig()
        logger.info("LLM 配置加载成功 (base_url=%s)", llm_config.base_url)
    except OSError as e:
        logger.error("LLM 配置加载失败: %s", str(e))
        sys.exit(1)

    # 初始化数据库
    db = DatabaseManager(cfg.db_path)
    db.init_db()
    logger.info("数据库已初始化: %s", cfg.db_path)

    # 初始化 ASR 引擎
    asr_engine = ASREngine(
        model_size=cfg.model_size,
        cpu_threads=cfg.cpu_threads,
    )
    logger.info("ASR 引擎已创建 (model=%s, cpu_threads=%d)", cfg.model_size, cfg.cpu_threads)

    # 线程池（max_workers=1 防止 CPU OOM）
    executor = ThreadPoolExecutor(max_workers=1)

    if cfg.once:
        # --once 模式：递归扫描 input_dir 中所有现有的 .mp4 文件
        logger.info("--once 模式: 递归扫描 %s", cfg.input_dir)
        input_path = Path(cfg.input_dir)
        if not input_path.exists():
            logger.error("输入目录不存在: %s", cfg.input_dir)
            sys.exit(1)

        video_files = sorted(input_path.rglob("*.mp4"))
        if not video_files:
            logger.info("没有找到待处理的 .mp4 文件")
            return

        logger.info("发现 %d 个 .mp4 文件", len(video_files))
        if cfg.limit > 0:
            logger.info("--limit=%d, 仅处理前 %d 个文件", cfg.limit, cfg.limit)
            video_files = video_files[:cfg.limit]
        for video_path in video_files:
            video_name = video_path.name
            video_id = hashlib.md5(str(video_path).encode("utf-8")).hexdigest()
            db.create_task(video_id, video_name, str(video_path))
            process_pipeline(
                video_id, video_name, str(video_path),
                db, asr_engine, llm_config, cfg,
            )
        logger.info("--once 模式完成")
        return

    # 持续监听模式（递归监听子目录）
    if not Path(cfg.input_dir).exists():
        logger.warning("输入目录不存在，正在创建: %s", cfg.input_dir)
        Path(cfg.input_dir).mkdir(parents=True, exist_ok=True)

    observer = start_watcher(cfg.input_dir, db, asr_engine, llm_config, cfg, executor)

    # 优雅关闭
    def shutdown(signum, frame):
        logger.info("收到关闭信号，正在停止...")
        observer.stop()
        executor.shutdown(wait=False)
        observer.join()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown(None, None)


if __name__ == "__main__":
    main()
