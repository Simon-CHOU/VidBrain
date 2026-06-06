"""
LangGraph Agent 工作流。

定义基于 DeepSeek API 的三阶段知识处理流程：
1. clean_and_extract：术语纠错 + 分段 + 提炼核心知识
2. auto_link：基于 Obsidian Vault 已有笔记生成 [[双链]]
3. suggest_update：分析是否需要更新关联已有笔记
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, TypedDict

from langgraph.graph import END, StateGraph
from openai import OpenAI

from vidbrain.audit import get_audit
from vidbrain.config import LLMConfig
from vidbrain.metrics import get_metrics

logger = logging.getLogger("vidbrain.agent")


class AgentState(TypedDict):
    """LangGraph Agent 状态。"""

    video_id: str
    video_name: str
    raw_text: str
    existing_notes: List[str]
    related_notes: List[dict]
    update_suggestions: List[dict]
    final_markdown: str
    feedback_context: str


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
            audit.api_call("deepseek", "chat.completions.create", elapsed_ms, success=False,
                          error=str(e))
            logger.warning("LLM 调用失败 (尝试 %d/%d): %s", attempt, max_retries, str(e))
            if attempt < max_retries:
                sleep_time = 2 ** (attempt - 1)
                time.sleep(sleep_time)
            else:
                raise


def create_agent_graph(llm_config: LLMConfig):
    """创建并编译 LangGraph Agent 工作流。"""
    client = OpenAI(api_key=llm_config.api_key, base_url=llm_config.base_url)
    model = llm_config.model
    # 注意：不要在日志中记录 api_key

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
        rest = notes[10:] if len(notes) > 10 else []
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

    def suggest_update_node(state: AgentState) -> Dict[str, Any]:
        """节点 3：分析是否需要更新关联已有笔记。"""
        related = state.get("related_notes", [])
        if not related:
            logger.info("[Agent] 无关联笔记，跳过更新建议")
            return {"update_suggestions": []}

        logger.info("[Agent] 生成更新建议 (关联 %d 篇笔记): %s", len(related), state["video_name"])

        # 构建关联笔记摘要
        related_summary_parts: list[str] = []
        for rn in related:
            preview = rn.get("content_preview", "")
            related_summary_parts.append(
                f"- 笔记: {rn['name']}\n  匹配术语: {', '.join(rn['match_terms'])}\n  内容预览: {preview[:200]}"
            )
        related_summary = "\n".join(related_summary_parts)

        new_preview = state["final_markdown"][:600]

        prompt = (
            f"Given this NEW note about \"{state['video_name']}\" and EXISTING related notes below, "
            "determine if each existing note should be updated.\n\n"
            "Options:\n"
            "- 'none': no update needed\n"
            "- 'ref': add a reference link at the bottom\n"
            "- 'supplement': add supplementary content\n\n"
            f"## New Note Content (preview)\n{new_preview}\n\n"
            f"## Existing Related Notes\n{related_summary}\n\n"
            "Output JSON:\n"
            '{"suggestions": [{"target_note": "<name>", "type": "ref|supplement|none", '
            '"content": "markdown text to append"}]}'
        )

        raw_json = _call_llm(client, model, prompt, temperature=0.1)
        try:
            import json
            import re as _re
            json_match = _re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_json, _re.DOTALL)
            if json_match:
                raw_json = json_match.group(1)
            result = json.loads(raw_json)
            suggestions = result.get("suggestions", [])
            filtered = [s for s in suggestions if s.get("type", "none") != "none"]
            logger.info("[Agent] 更新建议生成完成: %d 条", len(filtered))
            return {"update_suggestions": filtered}
        except Exception:
            logger.warning("[Agent] 解析更新建议 JSON 失败")
            return {"update_suggestions": []}

    workflow = StateGraph(AgentState)
    workflow.add_node("clean_and_extract", clean_and_extract_node)
    workflow.add_node("auto_link", auto_link_node)
    workflow.add_node("suggest_update", suggest_update_node)
    workflow.set_entry_point("clean_and_extract")
    workflow.add_edge("clean_and_extract", "auto_link")
    workflow.add_edge("auto_link", "suggest_update")
    workflow.add_edge("suggest_update", END)

    return workflow.compile()
