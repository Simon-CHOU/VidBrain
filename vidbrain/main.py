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
from vidbrain.audit import get_audit
from vidbrain.classifier import classify_video
from vidbrain.config import LLMConfig, PipelineConfig
from vidbrain.db import DatabaseManager
from vidbrain.logger import setup_logger
from vidbrain.metrics import get_metrics
from vidbrain.pipeline import process_pipeline
from vidbrain.singleton import acquire_singleton
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
    parser.add_argument("--priority", default="normal", choices=["normal", "below_normal", "idle"],
        help="进程优先级（默认: normal）")
    parser.add_argument("--video-cooldown", type=int, default=0,
        help="视频间冷却秒数（默认: 0，长时运行推荐 30）")
    parser.add_argument("--embedding", action="store_true", default=False,
        help="启用 embedding 语义检索和 MOC 聚类（需设置 DASHSCOPE_API_KEY 环境变量）")
    parser.add_argument("--parallel", type=int, default=0,
        help="并行处理的视频数（0=串行，推荐 2-3，利用空闲 RAM 和 CPU 余量）")
    parser.add_argument("--asr-backend", default="cpu", choices=["cpu", "vulkan"],
        help="ASR 后端 (默认: cpu)。vulkan 需要 whisper.cpp 编译 Vulkan 支持并设置 WHISPER_CLI_PATH")
    parser.add_argument("--metrics-interval", type=int, default=3600,
        help="指标快照落盘间隔（秒，默认: 3600=1小时）")
    parser.add_argument("--metrics-export-dir", default="reports",
        help="指标导出目录（默认: reports）")
    parser.add_argument("--audit-export", action="store_true", default=False,
        help="退出前导出审计日志为 JSON（不启用则仅写 audit.jsonl）")
    return parser.parse_args(argv)


def build_config(args: argparse.Namespace) -> PipelineConfig:
    """将 CLI 参数转换为 PipelineConfig。"""
    import multiprocessing

    cpu_threads = args.cpu_threads if args.cpu_threads > 0 else max(
        1, multiprocessing.cpu_count() - 1
    )
    interval_seconds = parse_interval(args.interval) if args.interval else 0
    auto_refine_every_hours = parse_interval(args.auto_refine_every) // 3600 if args.auto_refine_every else 0
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
        parallel_workers=args.parallel,
        asr_backend=args.asr_backend,
    )


def classify_all_pending(db: DatabaseManager, input_dir: str) -> None:
    """对所有未分类的视频执行文件名分类（批量操作）。"""

    input_path = Path(input_dir)
    if not input_path.exists():
        return

    # 批量扫描磁盘上的所有 .mp4 文件

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


def _init_embedding():
    """初始化 EmbeddingConfig。"""
    from vidbrain.config import EmbeddingConfig
    return EmbeddingConfig()


def _init_embedding_store(vault_path: str):
    """初始化 EmbeddingStore。"""
    from vidbrain.embedding import EmbeddingStore
    return EmbeddingStore(vault_path)


def _init_embedding_engine(emb_config):
    """初始化 EmbeddingEngine。"""
    from vidbrain.embedding import EmbeddingEngine
    return EmbeddingEngine(emb_config)


def process_batch(
    db: DatabaseManager,
    asr_engine: ASREngine,
    llm_config: LLMConfig,
    cfg: PipelineConfig,
    emb_config = None,
    emb_store = None,
) -> int:
    """处理一批技术视频（含自动重试失败的），返回处理数量。

    当 cfg.parallel_workers > 0 时，使用线程池并行处理多个视频，
    利用空闲 RAM 换取吞吐量提升。
    """
    # 先收集所有待处理任务
    retryable = db.get_failed_retryable()
    if retryable:
        logger.info("发现 %d 个可重试的失败任务，将自动重试", len(retryable))

    tasks = db.get_pending_tech_tasks(limit=cfg.batch_size)
    if not tasks and not retryable:
        logger.info("没有待处理的 tech 视频")
        return 0

    all_tasks = list(retryable) + list(tasks)
    total = len(all_tasks)
    n_retryable = len(retryable)

    if cfg.parallel_workers <= 0:
        # ── 串行模式 ──
        logger.info("本批处理 %d 个 tech 视频 (串行)", total)
        for task in all_tasks:
            process_pipeline(
                task["id"], task["video_name"], task["file_path"],
                db, asr_engine, llm_config, cfg,
                embedding_config=emb_config,
                embedding_store=emb_store,
            )
            if cfg.video_cooldown > 0:
                from vidbrain.throttle import cooldown_sleep
                cooldown_sleep(cfg.video_cooldown, f"视频 {task['video_name']} 处理完成")
    else:
        # ── 并行模式 ──
        workers = min(cfg.parallel_workers, total)
        logger.info("本批处理 %d 个 tech 视频 (并行, workers=%d)", total, workers)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {}
            for task in all_tasks:
                future = executor.submit(
                    process_pipeline,
                    task["id"], task["video_name"], task["file_path"],
                    db, asr_engine, llm_config, cfg,
                    emb_config, emb_store,
                )
                futures[future] = task["video_name"]

            # 等待所有任务完成
            for future in futures:
                video_name = futures[future]
                try:
                    future.result()  # 传播异常（如有）
                except Exception as e:
                    logger.error("[Parallel] 任务异常: %s - %s", video_name, str(e))

        # 并行批次的冷却（整批完成后再冷却）
        if cfg.video_cooldown > 0:
            from vidbrain.throttle import cooldown_sleep
            cooldown_sleep(cfg.video_cooldown, "并行批次处理完成")

    return total


def retry_failed_tasks(db: DatabaseManager) -> int:
    """手动重置所有可重试的失败任务为 PENDING。"""
    retryable = db.get_failed_retryable()
    if not retryable:
        logger.info("没有可重试的失败任务")
        return 0
    for task in retryable:
        db.reset_retry(task["id"])
        logger.info("  已重置: %s", task["video_name"])
    logger.info("共重置 %d 个失败任务为 PENDING", len(retryable))
    return len(retryable)


def run_refine(
    cfg: PipelineConfig,
    embedding_store = None,
    embedding_engine = None,
) -> None:
    """执行知识库精炼模式。"""
    from vidbrain.refiner import refine_vault
    vault_path = Path(cfg.vault_dir)
    if not vault_path.exists():
        logger.error("Vault 目录不存在: %s", cfg.vault_dir)
        return
    llm_config = LLMConfig()
    refine_vault(str(vault_path), llm_config,
                 embedding_store=embedding_store,
                 embedding_engine=embedding_engine)


def _prompt_choice(prompt: str, options: str) -> str:
    """交互式提示，返回用户输入的选项（大写）。"""
    try:
        choice = input(f"{prompt} [{options}]: ").strip().upper()
    except (EOFError, KeyboardInterrupt):
        return "Q"
    return choice


def review_classifications(db: DatabaseManager) -> None:
    """交互式审核 unclear 分类的视频。"""
    from vidbrain.classifier import classify_video

    unclear = []
    with db._lock:
        import sqlite3
        with sqlite3.connect(db._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, video_name, file_path FROM video_pipeline WHERE category='unclear'"
            ).fetchall()
            unclear = [(r["id"], r["video_name"], r["file_path"]) for r in rows]

    if not unclear:
        logger.info("没有 unclear 视频需要审核")
        return

    logger.info("=" * 50)
    logger.info("分类审核：共 %d 个 unclear 视频", len(unclear))
    logger.info("=" * 50)

    for idx, (vid, name, fp) in enumerate(unclear, 1):
        print(f"\n[{idx}/{len(unclear)}] {name}")
        choice = _prompt_choice("  [T]ech  [S]kip  [P]ass  [Q]uit", "T/S/P/Q")
        if choice == "T":
            db.classify_task(vid, "tech", "人工审核: 标记为 tech")
            logger.info("  -> tech")
        elif choice == "S":
            db.classify_task(vid, "skip", "人工审核: 标记为 skip")
            logger.info("  -> skip")
        elif choice == "Q":
            logger.info("审核中断，剩余 %d 个未处理", len(unclear) - idx)
            break
        else:
            logger.info("  -> 保留 unclear")

    logger.info("分类审核完成")


def review_queue(db: DatabaseManager, batch_size: int) -> list[str]:
    """交互式审批处理队列，返回已审批的视频 ID 列表。

    Args:
        db: 数据库管理器
        batch_size: 每批最大数量

    Returns:
        用户 approved 的视频 ID 列表
    """
    tasks = db.get_pending_tech_tasks(limit=max(batch_size, 50))
    if not tasks:
        logger.info("没有待处理的 tech 视频")
        return []

    logger.info("=" * 50)
    logger.info("队列审批：共 %d 个待处理视频", len(tasks))
    logger.info("选项: [A]pprove 批准  [S]kip 跳过  [R]eject 拒绝  [全部]通过  [Q]uit")
    logger.info("=" * 50)

    approved: list[str] = []
    for idx, task in enumerate(tasks, 1):
        reason = task.get("classify_reason", "")
        print(f"\n[{idx}/{len(tasks)}] {task['video_name']}")
        if reason:
            print(f"     分类理由: {reason}")
        choice = _prompt_choice("  [A]pprove  [S]kip  [R]eject  [*]全部通过  [Q]uit", "A/S/R/*/Q")
        if choice == "A":
            approved.append(task["id"])
            logger.info("  -> 已批准")
        elif choice == "S":
            db.classify_task(task["id"], "skip", "人工审批: 跳过")
            logger.info("  -> 跳过")
        elif choice == "R":
            db.classify_task(task["id"], "skip", "人工审批: 拒绝")
            logger.info("  -> 拒绝")
        elif choice == "*":
            # 全部通过剩余
            for t in tasks[idx - 1:]:
                approved.append(t["id"])
            logger.info("  -> 全部通过 (%d 个)", len(tasks) - idx + 1)
            break
        elif choice == "Q":
            logger.info("  -> 审批中断")
            break

    logger.info("队列审批完成: 批准 %d 个视频", len(approved))
    return approved


def review_drafts_vault(cfg: PipelineConfig, db: DatabaseManager) -> None:
    """交互式审核草稿。"""
    from vidbrain.drafts import list_drafts, publish_draft, discard_draft

    drafts = list_drafts(cfg.vault_dir)
    if not drafts:
        logger.info("没有草稿需要审核")
        return

    logger.info("=" * 50)
    logger.info("草稿审核：共 %d 篇草稿", len(drafts))
    logger.info("选项: [P]ublish 发布  [D]iscard 删除  [S]kip 保留  [*]全部发布  [Q]uit")
    logger.info("=" * 50)

    for idx, draft_name in enumerate(drafts, 1):
        # 读取草稿文件名（去掉 .md 后缀作为显示）
        stem = Path(draft_name).stem
        print(f"\n[{idx}/{len(drafts)}] {stem}")
        choice = _prompt_choice("  [P]ublish  [D]iscard  [S]kip  [*]全部发布  [Q]uit", "P/D/S/*/Q")
        if choice == "P":
            result = publish_draft(cfg.vault_dir, draft_name)
            if result:
                # 尝试通过文件名反查 DB 任务并更新状态
                db.update_status_by_name(stem, "SUCCESS")
                logger.info("  -> 已发布")
        elif choice == "D":
            discard_draft(cfg.vault_dir, draft_name)
            db.update_status_by_name(stem, "DISCARDED")
            logger.info("  -> 已删除")
        elif choice == "*":
            for dn in drafts[idx - 1:]:
                publish_draft(cfg.vault_dir, dn)
                db.update_status_by_name(Path(dn).stem, "SUCCESS")
            logger.info("  -> 全部发布 (%d 篇)", len(drafts) - idx + 1)
            break
        elif choice == "Q":
            logger.info("  -> 审核中断")
            break
        else:
            logger.info("  -> 保留草稿")

    logger.info("草稿审核完成")


def process_approved_tasks(
    approved_ids: list[str],
    db: DatabaseManager,
    asr_engine: ASREngine,
    llm_config: LLMConfig,
    cfg: PipelineConfig,
) -> int:
    """仅处理审批通过的视频。"""
    if not approved_ids:
        return 0
    count = 0
    for vid in approved_ids:
        task = db.get_task(vid)
        if task and task.get("status") == "PENDING":
            process_pipeline(
                task["id"], task["video_name"], task["file_path"],
                db, asr_engine, llm_config, cfg,
            )
            count += 1
    return count


def main(argv: list[str] | None = None) -> None:
    """主入口函数。"""
    args = parse_args(argv)
    cfg = build_config(args)

    # 初始化日志
    setup_logger()
    logger.info("VidBrain 启动")

    # 进程级单实例锁（必须在启动任何重量级操作前获取）
    acquire_singleton()

    # 初始化数据库
    db = DatabaseManager(cfg.db_path)
    db.init_db()
    logger.info("数据库已初始化: %s", cfg.db_path)

    # 初始化 Metrics 系统
    metrics = get_metrics()
    metrics.bind_db(db, cfg.db_path)
    logger.info("指标收集器已初始化")

    # 初始化 Audit 系统
    audit = get_audit()
    audit.setup(log_dir="logs", db=db)
    audit.system_event("startup", {"mode": "continuous" if cfg.interval_seconds > 0 else "once",
                                    "config": str(cfg)})
    logger.info("审计日志系统已初始化")

    # 设置进程优先级
    from vidbrain.throttle import set_low_priority
    set_low_priority(cfg.priority_level)

    # 精炼模式
    if cfg.refine:
        if cfg.embedding_enabled:
            emb_cfg = _init_embedding()
            emb_store = _init_embedding_store(cfg.vault_dir)
            emb_engine = _init_embedding_engine(emb_cfg)
            run_refine(cfg, emb_store, emb_engine)
        else:
            run_refine(cfg)
        return

    # 初始化 LLM 配置
    try:
        llm_config = LLMConfig()
        logger.info("LLM 配置加载成功 (base_url=%s)", llm_config.base_url)
    except OSError as e:
        logger.error("LLM 配置加载失败: %s", str(e))
        sys.exit(1)

    # 提前下载 ASR 模型（在首条任务处理之前完成下载，避免处理中途失败）
    # 并行模式下按比例缩减线程数，避免过多的并发线程导致上下文切换开销
    if cfg.parallel_workers > 0:
        effective_threads = max(2, cfg.cpu_threads // cfg.parallel_workers)
        logger.info("并行模式: workers=%d, cpu_threads 调整为 %d (原 %d)",
                    cfg.parallel_workers, effective_threads, cfg.cpu_threads)
        cfg.cpu_threads = effective_threads

    use_vulkan = cfg.asr_backend == "vulkan"

    # 预下载/准备模型
    if use_vulkan:
        logger.info("ASR 后端: Vulkan (whisper.cpp)")
        from vidbrain.asr_engine_vulkan import ASREngineVulkan
        # Vulkan 引擎自带 fallback，不需要单独下载 CPU 模型
        # 但仍然预下载 faster-whisper 模型作为安全的 fallback
        logger.info("预下载 CPU 备用模型: size=%s, cpu_threads=%d", cfg.model_size, cfg.cpu_threads)
        try:
            ASREngine.prepare_model(cfg.model_size, cfg.cpu_threads)
        except Exception:
            logger.warning("CPU 备用模型预下载失败（Vulkan 模式下非致命）")
    else:
        logger.info("ASR 后端: CPU (faster-whisper)")
        logger.info("预下载 ASR 模型: size=%s, cpu_threads=%d", cfg.model_size, cfg.cpu_threads)
        try:
            ASREngine.prepare_model(cfg.model_size, cfg.cpu_threads)
        except Exception:
            logger.exception("ASR 模型预下载失败，程序退出")
            sys.exit(1)

    # 初始化 embedding（可选）
    emb_config = None
    emb_store = None
    if cfg.embedding_enabled:
        emb_config = _init_embedding()
        emb_store = _init_embedding_store(cfg.vault_dir)

    # 阶段一：分类（对所有文件执行文件名分类）
    logger.info("阶段一: 分类视频文件...")
    classify_all_pending(db, cfg.input_dir)
    print_classification_summary(db)

    # 分类仅模式
    if cfg.classify_only:
        logger.info("--classify-only 模式完成")
        return

    # 独立分类审核模式
    if cfg.review_classifications and not cfg.semi:
        review_classifications(db)
        logger.info("--review-classifications 模式完成")
        return

    # 独立草稿审核模式
    if cfg.review_drafts and not cfg.semi:
        review_drafts_vault(cfg, db)
        logger.info("--review-drafts 模式完成")
        return

    # 初始化 ASR 引擎
    if use_vulkan:
        asr_engine = ASREngineVulkan(
            model_size=cfg.model_size,
            cpu_threads=cfg.cpu_threads,
        )
        if asr_engine.vulkan_available:
            logger.info("Vulkan ASR 引擎已就绪 (model=%s, cpu_threads=%d)", cfg.model_size, cfg.cpu_threads)
        else:
            logger.warning("Vulkan 不可用，将使用 faster-whisper CPU 作为降级方案")
    else:
        asr_engine = ASREngine(
            model_size=cfg.model_size,
            cpu_threads=cfg.cpu_threads,
        )
    logger.info("ASR 引擎已创建 (model=%s, cpu_threads=%d, backend=%s)",
                cfg.model_size, cfg.cpu_threads, cfg.asr_backend)

    # ── 半自动模式 ──
    if cfg.semi:
        # Step 1: 分类审核
        review_classifications(db)

        # Step 2: 队列审批
        approved = review_queue(db, cfg.batch_size)
        if not approved:
            logger.info("没有审批通过的视频，半自动模式结束")
            return

        # Step 3: 处理已审批的视频
        logger.info("开始处理 %d 个已审批视频", len(approved))
        process_approved_tasks(approved, db, asr_engine, llm_config, cfg)

        # Step 4: 草稿审核
        review_drafts_vault(cfg, db)
        logger.info("半自动模式完成")
        return

    # ── 全自动模式 ──
    # 阶段二：处理一批 tech 视频
    # 手动重试模式
    if cfg.retry_failed:
        count = retry_failed_tasks(db)
        if count == 0:
            logger.info("--retry-failed 模式完成，未发现可重试任务")
            return
        logger.info("已重置 %d 个任务，继续执行正常处理流程...", count)

    processed = process_batch(db, asr_engine, llm_config, cfg, emb_config, emb_store)
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
    executor = ThreadPoolExecutor(max_workers=max(1, cfg.parallel_workers))

    # 启动 watchdog 监听新文件
    Path(cfg.input_dir).mkdir(parents=True, exist_ok=True)
    observer = start_watcher(cfg.input_dir, db, asr_engine, llm_config, cfg, executor)

    last_metrics_flush = time.time()
    metrics_interval = args.metrics_interval

    def shutdown(signum, frame):
        logger.info("收到关闭信号，正在停止...")
        # 最终指标落盘
        metrics.log_summary()
        metrics.flush_to_db()
        metrics.dump_json(f"{args.metrics_export_dir}/metrics_final.json")
        # 审计日志导出
        if args.audit_export:
            audit.dump_json(f"{args.metrics_export_dir}/audit_final.json")
        audit.system_event("shutdown", {"uptime_s": round(time.time() - metrics._start_time, 1)})
        observer.stop()
        executor.shutdown(wait=False)
        observer.join()
        logger.info("VidBrain 已停止")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        batch_count = 0
        last_refine_time = time.time()
        while True:
            time.sleep(cfg.interval_seconds)
            logger.info("定时触发: 开始新一批处理")
            # 先分类新文件
            classify_all_pending(db, cfg.input_dir)
            # 再处理一批
            batch_processed = process_batch(db, asr_engine, llm_config, cfg, emb_config, emb_store)
            batch_count += 1
            metrics.incr("batches_completed")
            metrics.mark_event("last_batch_time")

            # 定期落盘指标
            now = time.time()
            if now - last_metrics_flush >= metrics_interval:
                metrics.flush_to_db()
                metrics.dump_json(f"{args.metrics_export_dir}/metrics_snapshot.json")
                metrics.log_summary()
                last_metrics_flush = now

            # 检查是否需要自动精炼
            need_refine = False
            refine_reason = ""
            if cfg.auto_refine_after > 0 and batch_count % cfg.auto_refine_after == 0:
                need_refine = True
                refine_reason = f"已处理 {batch_count} 批"
            elif cfg.auto_refine_every_hours > 0:
                elapsed = time.time() - last_refine_time
                if elapsed >= cfg.auto_refine_every_hours * 3600:
                    need_refine = True
                    refine_reason = f"距上次精炼已过 {elapsed / 3600:.1f} 小时"

            if need_refine:
                logger.info("自动触发知识库精炼: %s", refine_reason)
                if cfg.embedding_enabled and emb_config is not None:
                    emb_engine = _init_embedding_engine(emb_config)
                    run_refine(cfg, emb_store, emb_engine)
                else:
                    run_refine(cfg)
                last_refine_time = time.time()
        # 正常退出（如 interval=0 的单次模式会到此处）
        metrics.log_summary()
        metrics.flush_to_db()
        metrics.dump_json(f"{args.metrics_export_dir}/metrics_final.json")
        if args.audit_export:
            audit.dump_json(f"{args.metrics_export_dir}/audit_final.json")
        audit.system_event("shutdown", {"reason": "completed"})
    except KeyboardInterrupt:
        shutdown(None, None)


if __name__ == "__main__":
    main()
