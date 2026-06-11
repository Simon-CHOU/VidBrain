"""Agent 状态定义模块。"""

from __future__ import annotations

from typing import Any, Dict, List, TypedDict


class AgentState(TypedDict, total=False):
    """LangGraph Agent 工作流状态。

    Attributes:
        video_id: 视频唯一标识符。
        video_name: 视频文件名。
        raw_text: ASR 转录的原始文本。
        existing_notes: 已有笔记 stem 列表（按质量降序排列）。
        related_notes: 关联笔记列表，每项包含 name, stem, match_terms, content_preview。
        final_markdown: Agent 生成的最终 Markdown 笔记。
        feedback_context: 用户反馈上下文片段。
    """

    video_id: str
    video_name: str
    raw_text: str
    existing_notes: List[str]
    related_notes: List[Dict[str, Any]]
    final_markdown: str
    feedback_context: str
