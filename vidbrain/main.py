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
from vidbrain.classifier import classify_video
from vidbrain.config import LLMConfig, PipelineConfig
from vidbrain.db import DatabaseManager
from vidbrain.logger import setup_logger
from vidbrain.pipeline import process_pipeline
from vidbrain.watcher import start_watcher

logger = logging.getLogger("vidbrain")


def parse_interval(value: str) -> int:
    """解析时间间隔字符串为秒数。支持格式: 30m, 2h, 3600 (纯数字=秒)。"""
    value = value.strip().lower()
    if value.endswith("h"):
        return int(value[:-1]) * 3600
    if value.endswith("m"):
        return int(value[:-1]) * 60
    if value.endswith("s"):
        return int(value[:-1])
    return int(value)


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
        default="tiny",
        help="Whisper 模型大小（默认: tiny）",
    )
    parser.add_argument(
        "--cpu-threads",
        type=int,
        default=0,
        help="ASR 使用的 CPU 线程数（默认: 逻辑核心数 - 1）",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5,
        help="每批处理的视频数（默认: 5）",
    )
    parser.add_argument(
        "--interval",
        default="",
        help="持续模式间隔（例如 30m, 2h），空=仅一次",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="处理一批后退出（与 --interval 互斥）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="最多处理的视频总数（0=不限制，默认: 0）",
    )
    parser.add_argument(
        "--classify-only",
        action="store_true",
        help="仅分类，不执行 ASR 和 Agent 处理",
    )
    parser.add_argument(
        "--refine",
        action="store_true",
        help="执行知识库精炼模式",
    )
    return parser.parse_args(argv)


def build_config(args: argparse.Namespace) -> PipelineConfig:
    """将 CLI 参数转换为 PipelineConfig。"""
    import multiprocessing

    cpu_threads = args.cpu_threads if args.cpu_threads > 0 else max(
        1, multiprocessing.cpu_count() - 1
    )
    interval_seconds = parse_interval(args.interval) if args.interval else 0
    return PipelineConfig(
        input_dir=args.input_dir,
        vault_dir=args.vault_dir,
        db_path=args.db_path,
        model_size=args.model_size,
        cpu_threads=cpu_threads,
        once=args.once or (interval_seconds == 0),
        limit=args.limit,
        batch_size=args.batch_size,
        interval_seconds=interval_seconds,
        classify_only=args.classify_only,
        refine=args.refine,
    )


def classify_all_pending(db: DatabaseManager, input_dir: str) -> None:
    """对所有未分类的视频执行文件名分类（批量操作）。"""
    from pathlib import Path

    input_path = Path(input_dir)
    if not input_path.exists():
        return

    # 批量扫描磁盘上的所有 .mp4 文件
    import hashlib

    all_mp4 = sorted(input_path.rglob("*.mp4"))

    # 批量查询已入库的文件路径
    known_paths = db.get_all_file_paths()

    # 批量准备新文件的插入数据
    new_batch: list[tuple[str, str, str, str, str]] = []
    for fp in all_mp4:
        fstr = str(fp)
        if fstr not in known_paths:
            name = fp.name
            vid = hashlib.md5(fstr.encode("utf-8")).hexdigest()
            cat, reason = classify_video(name)
            new_batch.append((vid, name, fstr, cat, reason))

    if new_batch:
        db.bulk_create_and_classify(new_batch)
        tech_count = sum(1 for _, _, _, c, _ in new_batch if c == "tech")
        logger.info(
            "新文件分类完成: 共 %d (tech=%d, skip=%d, unclear=%d)",
            len(new_batch), tech_count,
            sum(1 for _, _, _, c, _ in new_batch if c == "skip"),
            sum(1 for _, _, _, c, _ in new_batch if c == "unclear"),
        )

    # 批量处理已入库但未分类的
    uncategorized = db.get_uncategorized_ids()
    if uncategorized:
        logger.info("补分类 %d 个已入库文件...", len(uncategorized))
        update_batch: list[tuple[str, str, str]] = []
        for vid, name, fp in uncategorized:
            cat, reason = classify_video(name)
            update_batch.append((cat, reason, vid))
        db.bulk_update_classification(update_batch)
        logger.info("补分类完成")


def print_classification_summary(db: DatabaseManager) -> None:
    """打印分类统计摘要。"""
    cats = db.count_by_category()
    logger.info(
        "分类汇总: tech=%d, skip=%d, unclear=%d",
        cats.get("tech", 0), cats.get("skip", 0), cats.get("unclear", 0),
    )


def process_batch(
    db: DatabaseManager,
    asr_engine: ASREngine,
    llm_config: LLMConfig,
    cfg: PipelineConfig,
) -> int:
    """处理一批 tech 视频，返回处理数量。"""
    tasks = db.get_pending_tech_tasks(limit=cfg.batch_size)
    if not tasks:
        logger.info("没有待处理的 tech 视频")
        return 0

    logger.info("本批处理 %d 个 tech 视频", len(tasks))
    for task in tasks:
        process_pipeline(
            task["id"], task["video_name"], task["file_path"],
            db, asr_engine, llm_config, cfg,
        )
    return len(tasks)


def run_refine(cfg: PipelineConfig) -> None:
    """执行知识库精炼模式。"""
    from vidbrain.refiner import refine_vault
    vault_path = Path(cfg.vault_dir)
    if not vault_path.exists():
        logger.error("Vault 目录不存在: %s", cfg.vault_dir)
        return
    from vidbrain.config import LLMConfig
    llm_config = LLMConfig()
    refine_vault(str(vault_path), llm_config)


def main(argv: list[str] | None = None) -> None:
    """主入口函数。"""
    args = parse_args(argv)
    cfg = build_config(args)

    # 初始化日志
    setup_logger()
    logger.info("VidBrain 启动")

    # 精炼模式
    if cfg.refine:
        run_refine(cfg)
        return

    # 初始化 LLM 配置
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

    # 阶段一：分类（对所有文件执行文件名分类）
    logger.info("阶段一: 分类视频文件...")
    classify_all_pending(db, cfg.input_dir)
    print_classification_summary(db)

    # 分类仅模式
    if cfg.classify_only:
        logger.info("--classify-only 模式完成")
        return

    # 初始化 ASR 引擎
    asr_engine = ASREngine(
        model_size=cfg.model_size,
        cpu_threads=cfg.cpu_threads,
    )
    logger.info("ASR 引擎已创建 (model=%s, cpu_threads=%d)", cfg.model_size, cfg.cpu_threads)

    # 阶段二：处理一批 tech 视频
    processed = process_batch(db, asr_engine, llm_config, cfg)
    if processed == 0:
        logger.info("没有待处理的 tech 视频")

    # 单次模式：退出
    if cfg.interval_seconds == 0 or cfg.once:
        logger.info("单次模式完成")
        return

    # 持续模式：定时调度
    logger.info(
        "持续模式已启动，间隔 %d 秒，每批 %d 个视频",
        cfg.interval_seconds, cfg.batch_size,
    )
    executor = ThreadPoolExecutor(max_workers=1)

    # 启动 watchdog 监听新文件
    Path(cfg.input_dir).mkdir(parents=True, exist_ok=True)
    observer = start_watcher(cfg.input_dir, db, asr_engine, llm_config, cfg, executor)

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
            time.sleep(cfg.interval_seconds)
            logger.info("定时触发: 开始新一批处理")
            # 先分类新文件
            classify_all_pending(db, cfg.input_dir)
            # 再处理一批
            process_batch(db, asr_engine, llm_config, cfg)
    except KeyboardInterrupt:
        shutdown(None, None)


if __name__ == "__main__":
    main()
