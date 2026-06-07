"""
核心管线调度逻辑。

连接所有模块：ASR → Agent → 写入 Vault。

重要约束：程序永远不得修改 input_dir（即 I:/web-videos）下的任何文件。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path

from vidbrain.agent_graph import AgentState, create_agent_graph
from vidbrain.asr_engine import ASREngine
from vidbrain.audit import get_audit
from vidbrain.config import EmbeddingConfig, LLMConfig, PipelineConfig
from vidbrain.db import DatabaseManager
from vidbrain.drafts import write_draft
from vidbrain.metrics import get_metrics
from vidbrain.updater import check_and_update, check_related_notes
from vidbrain.feedback import detect_user_edits, extract_feedback_signals, get_feedback_context
from vidbrain.vault_cache import get_vault_cache

logger = logging.getLogger("vidbrain.pipeline")


def _compute_quality_score(
    asr_segments: int,
    final_markdown: str,
    user_edited: bool = False,
    reviewed: bool = False,
) -> int:
    """计算笔记质量评分 (0-10)。

    维度：
    - ASR 段数：<10=1分, 10-30=2分, >30=3分
    - 结构化程度：有 ## 标题=2分
    - 双链密度：≥3 个 [[链接]]=2分
    - 用户编辑过：=2分（正向信号）
    - 人工审核过：=1分
    """
    score = 0
    # ASR 信息量
    if asr_segments >= 30:
        score += 3
    elif asr_segments >= 10:
        score += 2
    elif asr_segments > 0:
        score += 1
    # 结构化程度
    if "\n##" in final_markdown or final_markdown.startswith("##"):
        score += 2
    # 双链密度
    if len(re.findall(r"\[\[[^]]+\]\]", final_markdown)) >= 3:
        score += 2
    elif len(re.findall(r"\[\[[^]]+\]\]", final_markdown)) >= 1:
        score += 1
    # 用户反馈信号
    if user_edited:
        score += 2
    if reviewed:
        score += 1
    return min(score, 10)


def _read_note_quality(vault_path: Path, note_stem: str) -> int:
    """从已有笔记 front-matter 读取质量评分。"""
    note_path = vault_path / f"{note_stem}.md"
    if not note_path.exists():
        return 0
    try:
        content = note_path.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"^quality_score:\s*(\d+)", content, re.MULTILINE)
        if match:
            return int(match.group(1))
    except Exception:
        pass
    return 0


def process_pipeline(
    video_id: str,
    video_name: str,
    file_path: str,
    db: DatabaseManager,
    asr_engine: ASREngine,
    llm_config: LLMConfig,
    cfg: PipelineConfig,
    embedding_config: EmbeddingConfig | None = None,
    embedding_store = None,
) -> None:
    """执行完整的视频处理管线。"""
    m = get_metrics()
    audit = get_audit()
    pipeline_start = time.time()

    logger.info("[Pipeline] 开始处理: %s", video_name)
    m.incr("total_processed")

    try:
        # Step 1: 本地 ASR
        prev_status = "PENDING"
        db.update_status(video_id, "ASR_PROCESSING")
        audit.task_status_change(video_id, video_name, prev_status, "ASR_PROCESSING")
        logger.info("[Pipeline] 阶段 1/4 - ASR 转录: %s", video_name)
        asr_start = time.time()
        asr_data = asr_engine.transcribe(file_path)
        asr_elapsed = time.time() - asr_start
        m.record_duration("asr_duration", asr_elapsed)
        raw_text = "\n".join(item["text"] for item in asr_data)
        db.update_status(video_id, "ASR_DONE", raw_asr=json.dumps(asr_data, ensure_ascii=False))
        audit.task_status_change(video_id, video_name, "ASR_PROCESSING", "ASR_DONE",
                                 details={"segments": len(asr_data), "duration_s": round(asr_elapsed, 1)})
        logger.info("[Pipeline] ASR 完成: %s, %d 段文本 (%.1fs)", video_name, len(asr_data), asr_elapsed)

        # Step 2: 扫描本地知识库建立动态 Context
        db.update_status(video_id, "AGENT_PROCESSING")
        audit.task_status_change(video_id, video_name, "ASR_DONE", "AGENT_PROCESSING")
        vault_path = Path(cfg.vault_dir)
        vault_cache = get_vault_cache()
        existing_notes = vault_cache.get_existing_notes(cfg.vault_dir)
        logger.info("[Pipeline] 阶段 2/4 - 扫描知识库: %s, 发现 %d 篇笔记 (缓存)", video_name, len(existing_notes))

        # 用户反馈检测
        edited_notes = detect_user_edits(cfg.vault_dir)
        feedback_signals = extract_feedback_signals(cfg.vault_dir, edited_notes)
        feedback_context = get_feedback_context(feedback_signals)
        if feedback_context:
            logger.info("[Pipeline] 用户反馈信号已提取 (%d 篇编辑, %d 篇审核)",
                        feedback_signals["edited_count"], feedback_signals["reviewed_count"])

        # Step 2.5: 检测关联笔记
        # 若 embedding 启用，初始化 store 并使用 embedding 检索
        embed_store_for_check = embedding_store
        embed_engine = None
        if cfg.embedding_enabled and embedding_config is not None:
            from vidbrain.embedding import EmbeddingEngine, EmbeddingStore
            if embed_store_for_check is None:
                embed_store_for_check = EmbeddingStore(str(vault_path))
            embed_engine = EmbeddingEngine(embedding_config)
        related_notes_list = check_related_notes(
            str(vault_path), video_name, raw_text, existing_notes,
            embedding_enabled=cfg.embedding_enabled,
            embedding_store=embed_store_for_check,
            embedding_engine=embed_engine,
        )

        # Step 3: 运行 Agent
        logger.info("[Pipeline] 阶段 3/4 - Agent 处理: %s", video_name)
        agent_start = time.time()
        graph = create_agent_graph(llm_config)
        initial_state: AgentState = {
            "video_id": video_id,
            "video_name": video_name,
            "raw_text": raw_text,
            "existing_notes": existing_notes,
            "related_notes": related_notes_list,
            "update_suggestions": [],
            "final_markdown": "",
            "feedback_context": feedback_context,
        }
        final_state = graph.invoke(initial_state)
        agent_elapsed = time.time() - agent_start
        m.record_duration("agent_duration", agent_elapsed)
        logger.info("[Pipeline] Agent 处理完成: %s (%.1fs)", video_name, agent_elapsed)

        # Agent 结果持久化：合并 ASR 段和 final_markdown 写入 DB
        # 确保即使随后崩溃也不丢失已消耗 Token 生成的 Agent 输出
        db.update_status(video_id, "AGENT_DONE", raw_asr=json.dumps({
            "asr_segments": asr_data,
            "final_markdown": final_state["final_markdown"],
        }, ensure_ascii=False))
        logger.info("[Pipeline] Agent 结果已持久化: %s", video_name)

        # Step 4: 写入 Obsidian Vault（永远不修改 input_dir 下的文件）
        logger.info("[Pipeline] 阶段 4/4 - 写入知识库: %s", video_name)
        output_file_name = f"{Path(video_name).stem}.md"
        vault_path.mkdir(parents=True, exist_ok=True)

        if cfg.semi:
            # 半自动模式：写入 _drafts/ 目录，等待人工审核
            write_draft(cfg.vault_dir, output_file_name, final_state["final_markdown"], video_name)
            db.update_status(video_id, "DRAFT_PENDING")
            audit.task_status_change(video_id, video_name, "AGENT_PROCESSING", "DRAFT_PENDING")
            logger.info("[Pipeline] 草稿已生成 (待审核): %s/%s", "_drafts", output_file_name)
        else:
            # 全自动模式：直接写入 Vault 根目录
            output_path = vault_path / output_file_name
            quality = _compute_quality_score(
                len(asr_data), final_state["final_markdown"],
            )
            front_matter = (
                f"---\n"
                f"type: technical-note\n"
                f"source_video: {video_name}\n"
                f"status: auto-generated\n"
                f"quality_score: {quality}\n"
                f"created: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"---\n\n"
            )
            full_content = front_matter + final_state["final_markdown"]

            # 原子写入：先写临时文件，再 rename，防止输出半截文件
            output_stem = Path(output_file_name).stem
            fd, tmp_path = tempfile.mkstemp(
                dir=str(vault_path), suffix=".md", prefix=".vidbrain_tmp_"
            )
            os.close(fd)
            try:
                Path(tmp_path).write_text(full_content, encoding="utf-8")
                shutil.move(tmp_path, str(output_path))
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

            # 增量更新 Vault 缓存
            vault_cache.add_note(cfg.vault_dir, output_stem, full_content)

            db.update_status(video_id, "SUCCESS")
            pipeline_elapsed = time.time() - pipeline_start
            m.record_duration("pipeline_total", pipeline_elapsed)
            m.incr("total_succeeded")
            audit.task_status_change(video_id, video_name, "AGENT_PROCESSING", "SUCCESS",
                                     details={"quality_score": quality,
                                              "segments": len(asr_data),
                                              "duration_s": round(pipeline_elapsed, 1)})
            audit.file_write(str(output_path), len(full_content.encode("utf-8")), video_name)
            logger.info("[Pipeline] 完成! %s -> %s (总计 %.1fs, quality=%d)",
                        video_name, output_path, pipeline_elapsed, quality)

            # 增量缓存新笔记的 embedding
            if cfg.embedding_enabled and embed_store_for_check is not None and embed_engine is not None:
                try:
                    vec = embed_engine.embed(full_content[:1000])
                    note_mtime = output_path.stat().st_mtime
                    embed_store_for_check.set_vector(
                        output_stem, vec,
                        datetime.fromtimestamp(note_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                    )
                    embed_store_for_check.save()
                    logger.info("[Pipeline] 已缓存 embedding: %s", output_stem)
                except Exception as e:
                    logger.warning("[Pipeline] embedding 缓存失败: %s", str(e))

            # Step 4.5: 增量内容更新 (仅全自动模式)
            if not cfg.semi:
                update_count = check_and_update(
                    cfg.vault_dir, video_name, final_state["final_markdown"],
                    existing_notes, llm_config,
                )
                if update_count > 0:
                    logger.info("[Pipeline] 增量更新完成: 更新了 %d 篇关联笔记", update_count)

    except Exception as e:
        error_msg = str(e)
        pipeline_elapsed = time.time() - pipeline_start
        m.incr("total_failed")
        audit.error("pipeline", error_msg, video_id=video_id, video_name=video_name,
                    details={"duration_s": round(pipeline_elapsed, 1)})
        retry_count = db.increment_retry(video_id, error_msg)
        if retry_count >= 3:
            logger.error(
                "[Pipeline] 永久失败 (重试 %d/3): %s - %s",
                retry_count, video_name, error_msg,
            )
            db.update_status(video_id, "PERMANENTLY_FAILED", error_msg=error_msg)
            m.incr("total_permanently_failed")
            audit.task_status_change(video_id, video_name, "AGENT_PROCESSING", "PERMANENTLY_FAILED",
                                     reason=error_msg)
        else:
            logger.error(
                "[Pipeline] 失败 (将自动重试, %d/3): %s - %s",
                retry_count, video_name, error_msg,
            )
            db.update_status(video_id, "PENDING")
            audit.task_status_change(video_id, video_name, "AGENT_PROCESSING", "PENDING",
                                     reason=f"retry {retry_count}/3: {error_msg}")
