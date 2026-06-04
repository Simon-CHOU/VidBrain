"""
核心管线调度逻辑。

连接所有模块：ASR → Agent → 写入 Vault。

重要约束：程序永远不得修改 input_dir（即 I:/web-videos）下的任何文件。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from vidbrain.agent_graph import AgentState, create_agent_graph
from vidbrain.asr_engine import ASREngine
from vidbrain.config import LLMConfig, PipelineConfig
from vidbrain.db import DatabaseManager

logger = logging.getLogger("vidbrain.pipeline")


def process_pipeline(
    video_id: str,
    video_name: str,
    file_path: str,
    db: DatabaseManager,
    asr_engine: ASREngine,
    llm_config: LLMConfig,
    cfg: PipelineConfig,
) -> None:
    """执行完整的视频处理管线。"""
    logger.info("[Pipeline] 开始处理: %s", video_name)

    try:
        # Step 1: 本地 ASR
        db.update_status(video_id, "ASR_PROCESSING")
        logger.info("[Pipeline] 阶段 1/4 - ASR 转录: %s", video_name)
        asr_data = asr_engine.transcribe(file_path)
        raw_text = "\n".join(item["text"] for item in asr_data)
        db.update_status(video_id, "ASR_DONE", raw_asr=json.dumps(asr_data, ensure_ascii=False))
        logger.info("[Pipeline] ASR 完成: %s, %d 段文本", video_name, len(asr_data))

        # Step 2: 扫描本地知识库建立动态 Context
        db.update_status(video_id, "AGENT_PROCESSING")
        vault_path = Path(cfg.vault_dir)
        existing_notes = [p.stem for p in vault_path.glob("*.md")] if vault_path.exists() else []
        logger.info("[Pipeline] 阶段 2/4 - 扫描知识库: %s, 发现 %d 篇笔记", video_name, len(existing_notes))

        # Step 3: 运行 Agent
        logger.info("[Pipeline] 阶段 3/4 - Agent 处理: %s", video_name)
        graph = create_agent_graph(llm_config)
        initial_state: AgentState = {
            "video_id": video_id,
            "video_name": video_name,
            "raw_text": raw_text,
            "existing_notes": existing_notes,
            "final_markdown": "",
        }
        final_state = graph.invoke(initial_state)
        logger.info("[Pipeline] Agent 处理完成: %s", video_name)

        # Step 4: 写入 Obsidian Vault（永远不修改 input_dir 下的文件）
        logger.info("[Pipeline] 阶段 4/4 - 写入知识库: %s", video_name)
        output_file_name = f"{Path(video_name).stem}.md"
        output_path = vault_path / output_file_name
        front_matter = (
            f"---\n"
            f"type: technical-note\n"
            f"source_video: {video_name}\n"
            f"status: auto-generated\n"
            f"created: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"---\n\n"
        )
        vault_path.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            front_matter + final_state["final_markdown"], encoding="utf-8"
        )

        # 标记完成（不移除输入文件，不修改 input_dir）
        db.update_status(video_id, "SUCCESS")
        logger.info("[Pipeline] 完成! %s -> %s", video_name, output_path)

    except Exception as e:
        logger.error("[Pipeline] 失败: %s - %s", video_name, str(e))
        db.update_status(video_id, "FAILED", error_msg=str(e))
