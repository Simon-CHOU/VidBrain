"""
VidBrain 主入口。

CLI 参数解析 + 各模块组装 + 启动。

重要约束：程序永远不得修改 input_dir 下的任何文件。
"""

from __future__ import annotations

import hashlib
import json
import logging
import signal
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from src.cli import build_config, parse_args
from src.models.config import LLMConfig, PipelineConfig, setup_environment
from src.services.asr_service import ASREngine
from src.services.classifier_service import classify_video
from src.services.drafts_service import discard_draft, list_drafts, publish_draft
from src.services.pipeline_service import process_pipeline
from src.services.remote_asr_service import (
    RemoteASRClient,
    RemoteASRError,
    RemoteFirstASREngine,
)
from src.utils.audit import get_audit
from src.utils.db import DatabaseManager
from src.utils.logger import setup_logger
from src.utils.metrics import get_metrics
from src.utils.singleton import acquire_singleton
from src.utils.watcher import start_watcher

logger = logging.getLogger("vidbrain")


def classify_all_pending(db: DatabaseManager, input_dir: str) -> None:
    """对所有未分类的视频执行文件名分类（批量操作）。

    Args:
        db: 数据库管理器实例。
        input_dir: 输入目录路径。
    """
    input_path = Path(input_dir)
    if not input_path.exists():
        return

    all_mp4 = sorted(input_path.rglob("*.mp4"))
    known_paths = db.get_all_file_paths()

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
            len(new_batch),
            tech_count,
            sum(1 for _, _, _, c, _ in new_batch if c == "skip"),
            sum(1 for _, _, _, c, _ in new_batch if c == "unclear"),
        )

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
        cats.get("tech", 0),
        cats.get("skip", 0),
        cats.get("unclear", 0),
    )


def _init_embedding():
    """初始化 EmbeddingConfig。"""
    from src.models.config import EmbeddingConfig

    return EmbeddingConfig()


def _init_embedding_store(vault_path: str):
    """初始化 EmbeddingStore。"""
    from src.services.embedding_service import EmbeddingStore

    return EmbeddingStore(vault_path)


def _init_embedding_engine(emb_config):
    """初始化 EmbeddingEngine。"""
    from src.services.embedding_service import EmbeddingEngine

    return EmbeddingEngine(emb_config)


def process_batch(
    db: DatabaseManager,
    asr_engine: ASREngine,
    llm_config: LLMConfig,
    cfg: PipelineConfig,
    emb_config=None,
    emb_store=None,
) -> int:
    """处理一批技术视频（含自动重试失败的），返回处理数量。

    当 cfg.parallel_workers > 0 时，使用线程池并行处理多个视频，
    利用空闲 RAM 换取吞吐量提升。

    重试机制：失败的任务由 process_pipeline 自动重置为 PENDING，
    下次 process_batch 自然拾取重试，最多 3 次后转为 PERMANENTLY_FAILED。

    Args:
        db: 数据库管理器。
        asr_engine: ASR 引擎。
        llm_config: LLM 配置。
        cfg: 管线配置。
        emb_config: 可选的 embedding 配置。
        emb_store: 可选的 embedding 存储。

    Returns:
        本批处理的视频数量。
    """
    tasks = db.get_pending_tech_tasks(limit=cfg.batch_size)
    if not tasks:
        logger.info("没有待处理的 tech 视频")
        return 0

    total = len(tasks)

    if cfg.parallel_workers <= 0:
        logger.info("本批处理 %d 个 tech 视频 (串行)", total)
        for task in tasks:
            process_pipeline(
                task["id"],
                task["video_name"],
                task["file_path"],
                db,
                asr_engine,
                llm_config,
                cfg,
                embedding_config=emb_config,
                embedding_store=emb_store,
            )
            if cfg.video_cooldown > 0:
                from src.utils.throttle import cooldown_sleep

                cooldown_sleep(cfg.video_cooldown, f"视频 {task['video_name']} 处理完成")
    else:
        workers = min(cfg.parallel_workers, total)
        logger.info("本批处理 %d 个 tech 视频 (并行, workers=%d)", total, workers)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {}
            for task in tasks:
                future = executor.submit(
                    process_pipeline,
                    task["id"],
                    task["video_name"],
                    task["file_path"],
                    db,
                    asr_engine,
                    llm_config,
                    cfg,
                    emb_config,
                    emb_store,
                )
                futures[future] = task["video_name"]

            for future in futures:
                video_name = futures[future]
                try:
                    future.result()
                except Exception as e:
                    logger.error("[Parallel] 任务异常: %s - %s", video_name, str(e))

        if cfg.video_cooldown > 0:
            from src.utils.throttle import cooldown_sleep

            cooldown_sleep(cfg.video_cooldown, "并行批次处理完成")

    return total


def retry_failed_tasks(db: DatabaseManager) -> int:
    """手动重置所有可重试的失败任务为 PENDING。

    Args:
        db: 数据库管理器。

    Returns:
        重置的任务数。
    """
    retryable = db.get_failed_retryable()
    if not retryable:
        logger.info("没有可重试的失败任务")
        return 0
    for task in retryable:
        db.reset_retry(task["id"])
        logger.info("  已重置: %s", task["video_name"])
    logger.info("共重置 %d 个失败任务为 PENDING", len(retryable))
    return len(retryable)


def run_refine(cfg: PipelineConfig, embedding_store=None, embedding_engine=None) -> None:
    """执行知识库精炼模式。

    Args:
        cfg: 管线配置。
        embedding_store: 可选的 embedding 存储。
        embedding_engine: 可选的 embedding 引擎。
    """
    from src.services.refiner_service import refine_vault

    vault_path = Path(cfg.vault_dir)
    if not vault_path.exists():
        logger.error("Vault 目录不存在: %s", cfg.vault_dir)
        return
    llm_config = LLMConfig()
    refine_vault(
        str(vault_path),
        llm_config,
        embedding_store=embedding_store,
        embedding_engine=embedding_engine,
    )


def _prompt_choice(prompt: str, options: str) -> str:
    """交互式提示，返回用户输入的选项（大写）。

    Args:
        prompt: 提示文本。
        options: 可选选项字符串。

    Returns:
        用户输入的大写字母，Q 表示退出。
    """
    try:
        choice = input(f"{prompt} [{options}]: ").strip().upper()
    except (EOFError, KeyboardInterrupt):
        return "Q"
    return choice


def review_classifications(db: DatabaseManager) -> None:
    """交互式审核 unclear 分类的视频。"""
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
        db: 数据库管理器。
        batch_size: 每批最大数量。

    Returns:
        用户 approved 的视频 ID 列表。
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
            for t in tasks[idx - 1 :]:
                approved.append(t["id"])
            logger.info("  -> 全部通过 (%d 个)", len(tasks) - idx + 1)
            break
        elif choice == "Q":
            logger.info("  -> 审批中断")
            break

    logger.info("队列审批完成: 批准 %d 个视频", len(approved))
    return approved


def review_drafts_vault(cfg: PipelineConfig, db: DatabaseManager) -> None:
    """交互式审核草稿。

    Args:
        cfg: 管线配置。
        db: 数据库管理器。
    """
    drafts = list_drafts(cfg.vault_dir)
    if not drafts:
        logger.info("没有草稿需要审核")
        return

    logger.info("=" * 50)
    logger.info("草稿审核：共 %d 篇草稿", len(drafts))
    logger.info("选项: [P]ublish 发布  [D]iscard 删除  [S]kip 保留  [*]全部发布  [Q]uit")
    logger.info("=" * 50)

    for idx, draft_name in enumerate(drafts, 1):
        stem = Path(draft_name).stem
        print(f"\n[{idx}/{len(drafts)}] {stem}")
        choice = _prompt_choice("  [P]ublish  [D]iscard  [S]kip  [*]全部发布  [Q]uit", "P/D/S/*/Q")
        if choice == "P":
            result = publish_draft(cfg.vault_dir, draft_name)
            if result:
                db.update_status_by_name(stem, "SUCCESS")
                logger.info("  -> 已发布")
        elif choice == "D":
            discard_draft(cfg.vault_dir, draft_name)
            db.update_status_by_name(stem, "DISCARDED")
            logger.info("  -> 已删除")
        elif choice == "*":
            for dn in drafts[idx - 1 :]:
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
    """仅处理审批通过的视频。

    Args:
        approved_ids: 已审批通过的视频 ID 列表。
        db: 数据库管理器。
        asr_engine: ASR 引擎。
        llm_config: LLM 配置。
        cfg: 管线配置。

    Returns:
        处理的视频数量。
    """
    if not approved_ids:
        return 0
    count = 0
    for vid in approved_ids:
        task = db.get_task(vid)
        if task and task.get("status") == "PENDING":
            process_pipeline(
                task["id"],
                task["video_name"],
                task["file_path"],
                db,
                asr_engine,
                llm_config,
                cfg,
            )
            count += 1
    return count


def _should_refine_streaming(
    cfg: PipelineConfig, video_count: int, last_refine_time: float
) -> bool:
    """流式模式：检查是否应触发自动精炼。

    Args:
        cfg: 管线配置。
        video_count: 已处理视频数（未使用，保留接口一致性）。
        last_refine_time: 上次精炼时间戳。

    Returns:
        是否应触发精炼。
    """
    if cfg.auto_refine_every_hours > 0:
        elapsed = time.time() - last_refine_time
        return elapsed >= cfg.auto_refine_every_hours * 3600
    return False


def _should_refine_batches(cfg: PipelineConfig, batch_count: int, last_refine_time: float) -> bool:
    """定时模式：检查是否应触发自动精炼。

    Args:
        cfg: 管线配置。
        batch_count: 已完成的批次数。
        last_refine_time: 上次精炼时间戳。

    Returns:
        是否应触发精炼。
    """
    if cfg.auto_refine_after > 0 and batch_count % cfg.auto_refine_after == 0:
        return True
    if cfg.auto_refine_every_hours > 0:
        elapsed = time.time() - last_refine_time
        return elapsed >= cfg.auto_refine_every_hours * 3600
    return False


def _build_asr_engine(cfg: PipelineConfig) -> Any:
    """按配置初始化 ASR 引擎。"""
    if cfg.role == "primary" and cfg.remote_asr_host.strip():
        logger.info(
            "ASR 路由: 远端优先，失败回退本地 CPU (endpoint=%s:%d)",
            cfg.remote_asr_host,
            cfg.remote_asr_port,
        )
        logger.info(
            "预下载本地 CPU 回退模型: size=%s, cpu_threads=%d",
            cfg.model_size,
            cfg.cpu_threads,
        )
        try:
            ASREngine.prepare_model(cfg.model_size, cfg.cpu_threads)
        except Exception as exc:
            logger.exception("本地 CPU 回退模型预下载失败")
            raise RuntimeError("本地 CPU 回退模型预下载失败") from exc

        local_cpu_engine = ASREngine(
            model_size=cfg.model_size,
            cpu_threads=cfg.cpu_threads,
        )
        remote_engine = RemoteFirstASREngine(
            remote_client=RemoteASRClient(
                host=cfg.remote_asr_host,
                port=cfg.remote_asr_port,
                timeout_seconds=cfg.remote_asr_timeout_seconds,
            ),
            local_cpu_engine=local_cpu_engine,
            health_interval_seconds=cfg.remote_asr_health_interval_seconds,
            failure_threshold=cfg.remote_asr_failure_threshold,
            recovery_threshold=cfg.remote_asr_recovery_threshold,
            cooldown_seconds=cfg.remote_asr_cooldown_seconds,
        )
        try:
            remote_engine.bootstrap()
        except RemoteASRError as exc:
            logger.warning("远端 ASR worker 不可用，启动后将直接使用本地 CPU: %s", exc)

        logger.info(
            "ASR 引擎已创建 (model=%s, cpu_threads=%d, backend=remote-first->cpu)",
            cfg.model_size,
            cfg.cpu_threads,
        )
        return remote_engine

    use_vulkan = cfg.asr_backend == "vulkan"

    if use_vulkan:
        logger.info("ASR 后端: Vulkan (whisper.cpp)")
        from src.services.asr_vulkan_service import ASREngineVulkan

        logger.info(
            "预下载 CPU 备用模型: size=%s, cpu_threads=%d",
            cfg.model_size,
            cfg.cpu_threads,
        )
        try:
            ASREngine.prepare_model(cfg.model_size, cfg.cpu_threads)
        except Exception:
            logger.warning("CPU 备用模型预下载失败（Vulkan 模式下非致命）")

        asr_engine: Any = ASREngineVulkan(
            model_size=cfg.model_size,
            cpu_threads=cfg.cpu_threads,
        )
        if asr_engine.vulkan_available:
            logger.info(
                "Vulkan ASR 引擎已就绪 (model=%s, cpu_threads=%d)",
                cfg.model_size,
                cfg.cpu_threads,
            )
        else:
            logger.warning("Vulkan 不可用，将使用 faster-whisper CPU 作为降级方案")
    else:
        logger.info("ASR 后端: CPU (faster-whisper)")
        logger.info(
            "预下载 ASR 模型: size=%s, cpu_threads=%d",
            cfg.model_size,
            cfg.cpu_threads,
        )
        try:
            ASREngine.prepare_model(cfg.model_size, cfg.cpu_threads)
        except Exception as exc:
            logger.exception("ASR 模型预下载失败")
            raise RuntimeError("ASR 模型预下载失败") from exc

        asr_engine = ASREngine(
            model_size=cfg.model_size,
            cpu_threads=cfg.cpu_threads,
        )

    logger.info(
        "ASR 引擎已创建 (model=%s, cpu_threads=%d, backend=%s)",
        cfg.model_size,
        cfg.cpu_threads,
        cfg.asr_backend,
    )
    return asr_engine


def _read_json_request(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    """读取并解析 JSON 请求体。"""
    content_length = int(handler.headers.get("Content-Length", "0"))
    if content_length <= 0:
        raise ValueError("请求体为空")

    raw_body = handler.rfile.read(content_length)
    payload = json.loads(raw_body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("请求体必须是 JSON 对象")
    return payload


class _WorkerHandler(BaseHTTPRequestHandler):
    """worker 模式下的最小 HTTP 接口。"""

    server_version = "VidBrainWorker/0.1"
    asr_engine: Any = None
    cfg: PipelineConfig
    started_at: float = 0.0

    def log_message(self, format: str, *args: Any) -> None:
        logger.info("Worker HTTP - " + format, *args)

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _normalized_path(self) -> str:
        return self.path.split("?", maxsplit=1)[0].rstrip("/") or "/"

    def do_GET(self) -> None:  # noqa: N802
        if self._normalized_path() != "/healthz":
            self._send_json(
                {"status": "error", "message": "not found"},
                status=HTTPStatus.NOT_FOUND,
            )
            return

        cfg = type(self).cfg
        effective_backend = cfg.asr_backend
        if cfg.asr_backend == "vulkan":
            effective_backend = "vulkan" if type(self).asr_engine.vulkan_available else "cpu"

        self._send_json(
            {
                "status": "ok",
                "role": "worker",
                "backend": effective_backend,
                "model_size": cfg.model_size,
                "uptime_sec": round(time.time() - type(self).started_at, 2),
            }
        )

    def do_POST(self) -> None:  # noqa: N802
        if self._normalized_path() != "/inference":
            self._send_json(
                {"status": "error", "message": "not found"},
                status=HTTPStatus.NOT_FOUND,
            )
            return

        temp_file_path: str | None = None
        cfg = type(self).cfg
        try:
            content_type = self.headers.get("Content-Type", "")
            if content_type.startswith("application/json"):
                payload = _read_json_request(self)
                file_path = str(payload.get("file_path", "")).strip()
                if not file_path:
                    raise ValueError("缺少 file_path")
            else:
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length <= 0:
                    raise ValueError("请求体为空")
                raw_body = self.rfile.read(content_length)
                with tempfile.NamedTemporaryFile(
                    delete=False,
                    suffix=".wav",
                    prefix="vidbrain_worker_",
                ) as tmp_file:
                    tmp_file.write(raw_body)
                    temp_file_path = tmp_file.name
                file_path = temp_file_path

            target_path = Path(file_path)
            if not target_path.is_file():
                self._send_json(
                    {"status": "error", "message": f"文件不存在: {file_path}"},
                    status=HTTPStatus.NOT_FOUND,
                )
                return

            segments = type(self).asr_engine.transcribe(str(target_path))
            self._send_json(
                {
                    "status": "ok",
                    "segments": segments,
                    "backend": cfg.asr_backend,
                    "model_size": cfg.model_size,
                }
            )
        except ValueError as exc:
            self._send_json(
                {"status": "error", "message": str(exc)},
                status=HTTPStatus.BAD_REQUEST,
            )
        except json.JSONDecodeError:
            self._send_json(
                {"status": "error", "message": "JSON 解析失败"},
                status=HTTPStatus.BAD_REQUEST,
            )
        except Exception as exc:  # pragma: no cover - 防御性日志
            logger.exception("Worker ASR 请求处理失败: %s", exc)
            self._send_json(
                {"status": "error", "message": "ASR 推理失败"},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
        finally:
            if temp_file_path:
                Path(temp_file_path).unlink(missing_ok=True)


def _make_worker_handler(
    asr_engine: Any, cfg: PipelineConfig, started_at: float
) -> type[BaseHTTPRequestHandler]:
    """构造 worker 模式下使用的 HTTP handler。"""
    return type(
        "WorkerHandler",
        (_WorkerHandler,),
        {
            "asr_engine": asr_engine,
            "cfg": cfg,
            "started_at": started_at,
        },
    )


def _serve_worker(asr_engine: Any, cfg: PipelineConfig) -> None:
    """启动 worker 模式的最小 HTTP 服务面。"""
    started_at = time.time()
    handler_cls = _make_worker_handler(asr_engine, cfg, started_at)
    bind_host = "0.0.0.0"

    with ThreadingHTTPServer((bind_host, cfg.remote_asr_port), handler_cls) as server:
        bound_host, bound_port = server.server_address[:2]
        logger.info("Worker ASR 服务已启动: http://%s:%s", bound_host, bound_port)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            logger.info("收到中断信号，Worker ASR 服务准备退出")
        finally:
            logger.info("Worker ASR 服务已停止")


def run_worker(cfg: PipelineConfig) -> None:
    """worker 角色只启动 ASR 服务与健康接口。"""
    setup_logger()
    logger.info("VidBrain worker 启动")
    asr_engine = _build_asr_engine(cfg)
    _serve_worker(asr_engine, cfg)


def run_primary(args, cfg: PipelineConfig) -> None:  # noqa: C901
    """primary 角色运行完整主流程。"""
    setup_logger()
    logger.info("VidBrain 启动")

    acquire_singleton()

    db = DatabaseManager(cfg.db_path)
    db.init_db()
    logger.info("数据库已初始化: %s", cfg.db_path)

    recovered = db.recover_stuck_tasks()
    if recovered > 0:
        logger.warning("从异常中断恢复: 重置了 %d 个卡住的任务为 PENDING", recovered)
    else:
        logger.info("启动恢复检查: 无卡住任务")

    metrics = get_metrics()
    metrics.bind_db(db, cfg.db_path)
    logger.info("指标收集器已初始化")

    audit = get_audit()
    audit.setup(log_dir="logs", db=db)
    audit.system_event(
        "startup",
        {
            "mode": "continuous" if cfg.interval_seconds > 0 else "once",
            "config": str(cfg),
        },
    )
    logger.info("审计日志系统已初始化")

    from src.utils.throttle import PerformanceProfile

    perf_profile = PerformanceProfile(mode=cfg.profile)

    def _apply_profile_params() -> None:
        """将当前 profile 的运行参数应用到 cfg。"""
        params = perf_profile.get_params()
        cfg.parallel_workers = params["parallel_workers"]
        cfg.cpu_threads = params["cpu_threads_per_worker"]
        cfg.video_cooldown = params["video_cooldown_seconds"]

    if cfg.refine:
        if cfg.embedding_enabled:
            emb_cfg = _init_embedding()
            emb_store = _init_embedding_store(cfg.vault_dir)
            emb_engine = _init_embedding_engine(emb_cfg)
            run_refine(cfg, emb_store, emb_engine)
        else:
            run_refine(cfg)
        return

    try:
        llm_config = LLMConfig()
        logger.info("LLM 配置加载成功 (base_url=%s)", llm_config.base_url)
    except OSError as e:
        logger.error("LLM 配置加载失败: %s", str(e))
        sys.exit(1)

    if perf_profile.mode == "auto":
        perf_profile.evaluate()  # 启动即检测桌面活跃状态
        _apply_profile_params()
    # 无论 profile 模式，当 parallel_workers > 0 时都需分割 cpu_threads
    if cfg.parallel_workers > 0:
        effective_threads = max(2, cfg.cpu_threads // cfg.parallel_workers)
        logger.info(
            "并行模式: workers=%d, cpu_threads 调整为 %d (原 %d)",
            cfg.parallel_workers,
            effective_threads,
            cfg.cpu_threads,
        )
        cfg.cpu_threads = effective_threads

    emb_config = None
    emb_store = None
    if cfg.embedding_enabled:
        emb_config = _init_embedding()
        emb_store = _init_embedding_store(cfg.vault_dir)

    logger.info("阶段一: 分类视频文件...")
    classify_all_pending(db, cfg.input_dir)
    print_classification_summary(db)

    if cfg.classify_only:
        logger.info("--classify-only 模式完成")
        return

    if cfg.review_classifications and not cfg.semi:
        review_classifications(db)
        logger.info("--review-classifications 模式完成")
        return

    if cfg.review_drafts and not cfg.semi:
        review_drafts_vault(cfg, db)
        logger.info("--review-drafts 模式完成")
        return

    try:
        asr_engine = _build_asr_engine(cfg)
    except RuntimeError:
        sys.exit(1)

    if cfg.semi:
        review_classifications(db)
        approved = review_queue(db, cfg.batch_size)
        if not approved:
            logger.info("没有审批通过的视频，半自动模式结束")
            return
        logger.info("开始处理 %d 个已审批视频", len(approved))
        process_approved_tasks(approved, db, asr_engine, llm_config, cfg)
        review_drafts_vault(cfg, db)
        logger.info("半自动模式完成")
        return

    if cfg.retry_failed:
        count = retry_failed_tasks(db)
        if count == 0:
            logger.info("--retry-failed 模式完成，未发现可重试任务")
            return
        logger.info("已重置 %d 个任务，继续执行正常处理流程...", count)

    processed = process_batch(db, asr_engine, llm_config, cfg, emb_config, emb_store)
    if processed == 0:
        logger.info("没有待处理的 tech 视频")

    if cfg.interval_seconds == 0 or cfg.once:
        logger.info("单次模式完成")
        return

    executor = ThreadPoolExecutor(max_workers=max(1, cfg.parallel_workers))
    Path(cfg.input_dir).mkdir(parents=True, exist_ok=True)
    observer = start_watcher(cfg.input_dir, db, asr_engine, llm_config, cfg, executor)

    last_metrics_flush = time.time()
    metrics_interval = args.metrics_interval
    last_refine_time = time.time()
    video_count = 0

    _shutdown_requested = False

    def request_shutdown(signum, frame) -> None:
        nonlocal _shutdown_requested
        if not _shutdown_requested:
            _shutdown_requested = True
            logger.info("收到关闭信号 (signal=%d)，将在当前视频完成后优雅退出...", signum)

    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)

    def do_graceful_shutdown() -> None:
        """执行优雅退出流程。"""
        logger.info("等待正在进行中的任务完成（最多 5 分钟）...")
        executor.shutdown(wait=True, cancel_futures=False)
        observer.stop()
        observer.join(timeout=10)
        metrics.log_summary()
        metrics.flush_to_db()
        metrics.dump_json(f"{args.metrics_export_dir}/metrics_final.json")
        if args.audit_export:
            audit.dump_json(f"{args.metrics_export_dir}/audit_final.json")
        audit.system_event("shutdown", {"uptime_s": round(time.time() - metrics._start_time, 1)})
        logger.info("VidBrain 已优雅停止")

    if cfg.continuous:
        logger.info("流式持续模式已启动（处理完一个视频立即取下一个）")
        try:
            while True:
                if _shutdown_requested:
                    logger.info("执行优雅退出...")
                    break

                classify_all_pending(db, cfg.input_dir)
                tasks = db.get_pending_tech_tasks(limit=1)
                if not tasks:
                    logger.debug("无待处理任务，30s 后重试")
                    time.sleep(30)
                    continue

                if perf_profile.mode == "auto":
                    prev = perf_profile.current
                    new_profile = perf_profile.evaluate()
                    if new_profile != prev:
                        _apply_profile_params()
                        logger.info(
                            "Profile 参数已更新: workers=%d, cpu_threads=%d, cooldown=%ds",
                            cfg.parallel_workers,
                            cfg.cpu_threads,
                            cfg.video_cooldown,
                        )

                task = tasks[0]
                process_pipeline(
                    task["id"],
                    task["video_name"],
                    task["file_path"],
                    db,
                    asr_engine,
                    llm_config,
                    cfg,
                    embedding_config=emb_config,
                    embedding_store=emb_store,
                )
                video_count += 1
                metrics.incr("total_processed")

                now = time.time()
                if now - last_metrics_flush >= metrics_interval:
                    metrics.flush_to_db()
                    metrics.dump_json(f"{args.metrics_export_dir}/metrics_snapshot.json")
                    metrics.log_summary()
                    last_metrics_flush = now

                if _should_refine_streaming(cfg, video_count, last_refine_time):
                    logger.info("自动触发知识库精炼")
                    if cfg.embedding_enabled and emb_config is not None:
                        emb_engine = _init_embedding_engine(emb_config)
                        run_refine(cfg, emb_store, emb_engine)
                    else:
                        run_refine(cfg)
                    last_refine_time = time.time()

                if cfg.limit > 0 and video_count >= cfg.limit:
                    logger.info("已达到处理上限 %d，退出", cfg.limit)
                    break

        except KeyboardInterrupt:
            request_shutdown(None, None)
        finally:
            do_graceful_shutdown()
    else:
        logger.info(
            "持续模式已启动，间隔 %d 秒，每批 %d 个视频",
            cfg.interval_seconds,
            cfg.batch_size,
        )
        batch_count = 0
        try:
            while True:
                if _shutdown_requested:
                    logger.info("执行优雅退出...")
                    break
                time.sleep(cfg.interval_seconds)
                logger.info("定时触发: 开始新一批处理")
                classify_all_pending(db, cfg.input_dir)
                process_batch(db, asr_engine, llm_config, cfg, emb_config, emb_store)
                batch_count += 1
                metrics.incr("batches_completed")
                metrics.mark_event("last_batch_time")

                if perf_profile.mode == "auto":
                    prev = perf_profile.current
                    new_profile = perf_profile.evaluate()
                    if new_profile != prev:
                        _apply_profile_params()
                        logger.info(
                            "Profile 参数已更新: workers=%d, cpu_threads=%d, cooldown=%ds",
                            cfg.parallel_workers,
                            cfg.cpu_threads,
                            cfg.video_cooldown,
                        )

                now = time.time()
                if now - last_metrics_flush >= metrics_interval:
                    metrics.flush_to_db()
                    metrics.dump_json(f"{args.metrics_export_dir}/metrics_snapshot.json")
                    metrics.log_summary()
                    last_metrics_flush = now

                if _should_refine_batches(cfg, batch_count, last_refine_time):
                    logger.info("自动触发知识库精炼")
                    if cfg.embedding_enabled and emb_config is not None:
                        emb_engine = _init_embedding_engine(emb_config)
                        run_refine(cfg, emb_store, emb_engine)
                    else:
                        run_refine(cfg)
                    last_refine_time = time.time()

                if cfg.limit > 0 and batch_count * cfg.batch_size >= cfg.limit:
                    logger.info("已达到处理上限，退出")
                    break
        except KeyboardInterrupt:
            request_shutdown(None, None)
        finally:
            do_graceful_shutdown()


def main(argv: list[str] | None = None) -> None:
    """主入口函数。"""
    args = parse_args(argv)
    cfg = build_config(args)

    # 设置 HF 环境变量（必须在任何 HF 相关导入前调用）
    setup_environment()

    if cfg.role == "worker":
        run_worker(cfg)
        return

    run_primary(args, cfg)


if __name__ == "__main__":
    main()
