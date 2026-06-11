"""
LangGraph Agent 工作流。

定义基于 DeepSeek API 的两阶段知识处理流程：
1. clean_and_extract：术语纠错 + 分段 + 提炼核心知识
2. auto_link：基于 Obsidian Vault 已有笔记生成 [[双链]]

注：关联笔记更新由 updater_service.check_and_update() 独立负责，
不在 Agent 图内重复执行。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict

from langgraph.graph import END, StateGraph
from openai import OpenAI

from src.models.config import LLMConfig
from src.models.state import AgentState
from src.utils.audit import get_audit
from src.utils.metrics import get_metrics

logger = logging.getLogger("vidbrain.agent")

# 全局 OpenAI 客户端缓存，避免重复创建 HTTP 连接池
_openai_clients: Dict[str, OpenAI] = {}
_client_lock = threading.Lock()

# Agent 图编译缓存（避免每视频重复编译）
_cached_graph: tuple | None = None  # (cache_key, compiled_graph)
_graph_lock = threading.Lock()


def get_shared_client(api_key: str, base_url: str) -> OpenAI:
    """获取或创建共享的 OpenAI 客户端，按 (base_url, api_key_prefix) 缓存。"""
    cache_key = f"{base_url}::{api_key[:8]}"
    with _client_lock:
        if cache_key not in _openai_clients:
            _openai_clients[cache_key] = OpenAI(api_key=api_key, base_url=base_url)
        return _openai_clients[cache_key]


def _call_llm(client: OpenAI, model: str, prompt: str, temperature: float) -> str:
    """调用 LLM API，含重试机制（最多 3 次，指数退避）。"""
    m = get_metrics()
    audit = get_audit()
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        t0 = time.time()
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
            )
            elapsed_ms = (time.time() - t0) * 1000
            m.incr("llm_calls_total")
            m.record_duration("llm_call_duration", elapsed_ms / 1000)
            audit.api_call("deepseek", "chat.completions.create", elapsed_ms, success=True)
            return response.choices[0].message.content or ""
        except Exception as e:
            elapsed_ms = (time.time() - t0) * 1000
            m.incr("llm_calls_failed")
            audit.api_call(
                "deepseek", "chat.completions.create", elapsed_ms, success=False, error=str(e)
            )
            logger.warning("LLM 调用失败 (尝试 %d/%d): %s", attempt, max_retries, str(e))
            if attempt < max_retries:
                sleep_time = 2 ** (attempt - 1)
                time.sleep(sleep_time)
            else:
                raise


def create_agent_graph(llm_config: LLMConfig):  # noqa: C901
    """创建并编译 LangGraph Agent 工作流（编译结果被手动缓存）。"""
    # 手动缓存：LLMConfig 不可 hash，用 (api_key_prefix, base_url, model) 作为键
    global _cached_graph
    cache_key = (llm_config.api_key[:8], llm_config.base_url, llm_config.model)
    with _graph_lock:
        if _cached_graph is not None and _cached_graph[0] == cache_key:
            return _cached_graph[1]

    client = get_shared_client(llm_config.api_key, llm_config.base_url)
    model = llm_config.model

    def clean_and_extract_node(state: AgentState) -> Dict[str, Any]:
        """节点 1：术语纠错 + 分段 + 提炼核心知识。"""
        logger.info("[Agent] 清洗与提炼: %s", state["video_name"])
        prompt = (
            "你是一个资深的 AI Infrastructure 技术文档专家。请对以下技术视频的原始 ASR 文本进行处理：\n"
            "1. 修正错别字，尤其是专业技术术语（例如将'扣打'修正为'CUDA'，'卡夫卡'修正为'Kafka'，'单子融合'修正为'算子融合'）。\n"
            "2. 将无标点的文本根据语义进行结构化段落划分。\n"
            "3. 提炼出核心原理、架构设计或核心代码/逻辑片段。\n\n"
            f"原始 ASR 文本：\n{state['raw_text']}"
        )
        content = _call_llm(client, model, prompt, temperature=0.2)
        return {"final_markdown": content}

    def auto_link_node(state: AgentState) -> Dict[str, Any]:
        """节点 2：基于已有笔记生成双链。"""
        logger.info("[Agent] 自动织网: %s", state["video_name"])
        notes = state.get("existing_notes", [])
        # 前 10 个为高质量笔记（已按 quality_score 降序排列）
        preferred = notes[:10]
        notes_summary = ", ".join(notes) if notes else "（无已有笔记）"
        preferred_summary = ", ".join(preferred) if preferred else ""
        prompt = (
            "你是一个高级知识库架构师。请在不破坏原有 Markdown 结构的前提下，比对给定的'已有笔记列表'。\n"
            "如果当前技术笔记中出现了列表中已有的概念，请自动将其转换为 Obsidian 双链语法 `[[已存在的笔记]]`。\n"
            "如果发现非常关键、且列表里没有的全新技术名词，也请用 `[[新概念]]` 进行前瞻性标记。\n\n"
            f"已有笔记列表：{notes_summary}\n"
        )
        if preferred_summary:
            prompt += f"\n推荐优先链接（高质量笔记）：{preferred_summary}\n"
        prompt += f"\n当前笔记内容：\n{state['final_markdown']}"
        feedback = state.get("feedback_context", "")
        if feedback:
            prompt += f"\n\n用户反馈建议：\n{feedback}"
        content = _call_llm(client, model, prompt, temperature=0.1)
        return {"final_markdown": content}

    workflow = StateGraph(AgentState)
    workflow.add_node("clean_and_extract", clean_and_extract_node)
    workflow.add_node("auto_link", auto_link_node)
    workflow.set_entry_point("clean_and_extract")
    workflow.add_edge("clean_and_extract", "auto_link")
    workflow.add_edge("auto_link", END)

    compiled = workflow.compile()
    with _graph_lock:
        _cached_graph = (cache_key, compiled)
    return compiled
