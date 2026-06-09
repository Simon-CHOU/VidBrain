"""
VidBrain CLI 参数解析模块。

与 main.py 分离，使测试无需导入重量级依赖（faster_whisper, langgraph, openai 等）。
"""

from __future__ import annotations

import argparse
import multiprocessing

from src.models.config import PipelineConfig


def parse_interval(value: str) -> int:
    """解析时间间隔字符串为秒数。

    支持格式: 30m, 2h, 3600 (纯数字=秒)。

    Args:
        value: 时间间隔字符串。

    Returns:
        转换后的秒数。
    """
    value = value.strip().lower()
    if value.endswith("h"):
        return int(value[:-1]) * 3600
    if value.endswith("m"):
        return int(value[:-1]) * 60
    if value.endswith("s"):
        return int(value[:-1])
    return int(value)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析 CLI 参数。

    Args:
        argv: 命令行参数列表，None 时使用 sys.argv。

    Returns:
        解析后的参数命名空间。
    """
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
        default="./vidbrain_vault",
        help="Obsidian Vault 路径（默认: ./vidbrain_vault）",
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
        default="30m",
        help="持续模式间隔（例如 5m, 2h），默认: 30m",
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
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="手动重试所有可重试的失败任务（重置为 PENDING）",
    )
    parser.add_argument(
        "--auto-refine-after",
        type=int,
        default=0,
        help="处理 N 批后自动执行知识库精炼（0=禁用，默认: 0）",
    )
    parser.add_argument(
        "--auto-refine-every",
        default="",
        help="每 N 小时自动执行知识库精炼（例如 24h，0=禁用，默认: 空）",
    )
    parser.add_argument(
        "--semi",
        action="store_true",
        help="半自动模式（启用分类审核 + 队列审批 + 草稿审核）",
    )
    parser.add_argument(
        "--review-drafts",
        action="store_true",
        help="进入草稿审核模式",
    )
    parser.add_argument(
        "--review-classifications",
        action="store_true",
        help="进入分类审核模式",
    )
    parser.add_argument(
        "--priority",
        default="normal",
        choices=["normal", "below_normal", "idle"],
        help="进程优先级（默认: normal）",
    )
    parser.add_argument(
        "--video-cooldown",
        type=int,
        default=0,
        help="视频间冷却秒数（默认: 0，长时运行推荐 30）",
    )
    parser.add_argument(
        "--embedding",
        action="store_true",
        default=False,
        help="启用 embedding 语义检索和 MOC 聚类（需设置 DASHSCOPE_API_KEY 环境变量）",
    )
    parser.add_argument(
        "--chunk-all",
        action="store_true",
        default=False,
        help="一次性全量盘点 vault 中所有笔记的 chunk（需 --embedding）",
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=0,
        help="并行处理的视频数（0=串行，推荐 2-3，利用空闲 RAM 和 CPU 余量）",
    )
    parser.add_argument(
        "--asr-backend",
        default="cpu",
        choices=["cpu", "vulkan"],
        help="ASR 后端 (默认: cpu)。vulkan 需要 whisper.cpp 编译 Vulkan 支持并设置 WHISPER_CLI_PATH",
    )
    parser.add_argument(
        "--profile",
        default="auto",
        choices=["auto", "idle", "active"],
        help="性能 Profile (默认: auto 自动切换)。idle=满负荷, active=省电降速",
    )
    parser.add_argument(
        "--continuous",
        action="store_true",
        help="流式持续处理：处理完一个视频立即取下一个，不等待间隔。与 --interval 互斥",
    )
    parser.add_argument(
        "--metrics-interval",
        type=int,
        default=3600,
        help="指标快照落盘间隔（秒，默认: 3600=1小时）",
    )
    parser.add_argument(
        "--metrics-export-dir",
        default="reports",
        help="指标导出目录（默认: reports）",
    )
    parser.add_argument(
        "--audit-export",
        action="store_true",
        default=False,
        help="退出前导出审计日志为 JSON（不启用则仅写 audit.jsonl）",
    )
    return parser.parse_args(argv)


def build_config(args: argparse.Namespace) -> PipelineConfig:
    """将 CLI 参数转换为 PipelineConfig。

    Args:
        args: CLI 解析结果。

    Returns:
        配置好的 PipelineConfig 实例。
    """
    cpu_threads = (
        args.cpu_threads if args.cpu_threads > 0 else max(1, multiprocessing.cpu_count() - 1)
    )
    interval_seconds = parse_interval(args.interval) if args.interval else 0
    auto_refine_every_hours = (
        parse_interval(args.auto_refine_every) // 3600 if args.auto_refine_every else 0
    )
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
        auto_refine_after=args.auto_refine_after,
        auto_refine_every_hours=auto_refine_every_hours,
        retry_failed=args.retry_failed,
        semi=args.semi,
        review_drafts=args.review_drafts,
        review_classifications=args.review_classifications,
        priority_level=args.priority,
        video_cooldown=args.video_cooldown,
        embedding_enabled=args.embedding,
        chunk_all=args.chunk_all,
        parallel_workers=args.parallel,
        asr_backend=args.asr_backend,
        profile=args.profile,
        continuous=args.continuous,
    )
