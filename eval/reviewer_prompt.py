"""
LLM Reviewer prompt templates and response parsing.

Provides the system prompt (scoring rubric), user message template,
and JSON validation for the 4-dimension blind review.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger("vidbrain.eval")

REVIEW_SYSTEM_PROMPT = """你是一个技术文档质量评审专家。你的任务是对比两篇由 AI 生成的
技术笔记，从四个维度评判哪一篇更好。

## 身份规则
- 两篇笔记被标记为"笔记 A"和"笔记 B"。你不知道它们分别来自哪个系统。
- 你的评判必须基于证据，不能猜测来源。

## 评分规则
对每个维度独立打分（1-5 分），然后做综合判断。

### 维度 A：术语纠错准确度
- 输入：一份 ASR 原始文本 + 笔记 A + 笔记 B
- 首先，列出 ASR 文本中你认为存在错误或可疑的技术术语（至少 2 处）
- 然后，检查每篇笔记如何纠正这些术语
- 评分：
  1 = 把正确的术语改错了，或明显错误完全没纠正
  3 = 主要错误都纠正了，未引入新错误
  5 = 不仅纠正了错误，术语写法与常见技术文档风格一致
- 必须引用笔记中的原文片段作为证据

### 维度 B：双链相关性
- 输入：笔记 A 的 [[双链]] 列表 + 笔记 B 的 [[双链]] 列表 + 笔记全文
- 检查每个链接：
  - 上下文里确实提到了这个概念吗？（不瞎链）
  - 上下文里提到了已有笔记名但没链吗？（漏链）
  - 是否过度链接导致可读性下降？
- 评分：
  1 = 大量错误链接或严重漏链
  3 = 大部分链接合理，无重要遗漏
  5 = 每个链接精准命中，不漏不滥，密度恰到好处
- 必须列举至少 2 个具体的链接决策并判断对/错/漏

### 维度 C：更新建议质量
- 输入：笔记 A 的更新建议 + 笔记 B 的更新建议 + 关联笔记列表
- 检查每条建议：
  - 目标笔记是否真的与当前内容相关？
  - 更新类型（引用 ref / 补充 supplement）选择是否合理？
  - 补充内容是否言之有物？
  - 是否漏掉了该建议的更新？
  - 是否多出了无关的噪音建议？
- 如果两边都没有更新建议，此维度打 3 分（中性）
- 评分：
  1 = 建议全部不相关或内容空洞
  3 = 大部分建议合理
  5 = 每条建议都精准针对目标笔记的知识缺口

### 维度 D：综合质量
- 在完成 A/B/C 三个维度的独立评分后，做整体判断
- 考虑：知识提炼深度、段落结构逻辑性、可读性与实用性
- 输出：你倾向 A / 倾向 B / 持平
- 理由不超过 50 字
- 注意：长不等于好。简洁而准确 优于 冗长而空洞

## 输出格式
必须输出严格 JSON，不得包含任何其他文字：

{
  "asr_issues": ["ASR中识别到的术语问题1", "术语问题2"],
  "score_a": {
    "笔记A": {"分": 3, "证据": ["原文片段1", "原文片段2"]},
    "笔记B": {"分": 4, "证据": ["原文片段1", "原文片段2"]}
  },
  "score_b": {
    "笔记A": {"分": 3, "证据": [{"链接": "[[CUDA]]", "判断": "对", "理由": "..."}]},
    "笔记B": {"分": 4, "证据": [{"链接": "[[GPU]]", "判断": "漏", "理由": "..."}]}
  },
  "score_c": {
    "笔记A": {"分": 3, "证据": ["建议1的评估"]},
    "笔记B": {"分": 3, "证据": ["建议1的评估"]}
  },
  "score_d": {
    "偏好": "A",
    "理由": "不超过50字的理由"
  },
  "self_doubt": {
    "最可能出偏误的维度": "B",
    "原因": "不超过50字"
  }
}"""


def build_system_prompt() -> str:
    """Return the reviewer system prompt."""
    return REVIEW_SYSTEM_PROMPT


def build_user_message(
    raw_text: str,
    related_notes_summary: str,
    note_a_content: str,
    note_a_links: list[str],
    note_a_suggestions: list[dict],
    note_b_content: str,
    note_b_links: list[str],
    note_b_suggestions: list[dict],
) -> str:
    """Build the user message for a single A/B comparison.

    The A/B labels are randomized by the caller before passing data in.
    This function just renders the template — it does not know which is main/emb.
    """
    a_links_str = "\n".join(f"- {link}" for link in note_a_links) if note_a_links else "（无双链）"
    b_links_str = "\n".join(f"- {link}" for link in note_b_links) if note_b_links else "（无双链）"

    a_sug_str = json.dumps(note_a_suggestions, ensure_ascii=False, indent=2) if note_a_suggestions else "（无更新建议）"
    b_sug_str = json.dumps(note_b_suggestions, ensure_ascii=False, indent=2) if note_b_suggestions else "（无更新建议）"

    return f"""## ASR 原始文本
{raw_text}

---

## 关联笔记列表（两边 Agent 共用）
{related_notes_summary if related_notes_summary else "（无关联笔记）"}

---

## 笔记 A
### 完整内容
{note_a_content}

### 双链列表
{a_links_str}

### 更新建议
{a_sug_str}

---

## 笔记 B
### 完整内容
{note_b_content}

### 双链列表
{b_links_str}

### 更新建议
{b_sug_str}

---

请按照评分规则对比这两篇笔记，输出 JSON。"""


# Required top-level keys in the review JSON
_REQUIRED_KEYS = {"asr_issues", "score_a", "score_b", "score_c", "score_d", "self_doubt"}
_VALID_SCORES = {1, 2, 3, 4, 5}
_VALID_PREFERENCES = {"A", "B", "持平"}


def parse_review_response(raw: str) -> dict | None:
    """Parse and validate the LLM Reviewer JSON response.

    Returns the parsed dict if valid, None otherwise.
    """
    # Strip markdown code fences if present
    cleaned = raw.strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if m:
        cleaned = m.group(1)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("Reviewer 输出非合法 JSON")
        return None

    # Validate required keys
    if not _REQUIRED_KEYS.issubset(data.keys()):
        missing = _REQUIRED_KEYS - data.keys()
        logger.warning("Reviewer JSON 缺少字段: %s", missing)
        return None

    # Validate score_a
    for side in ("笔记A", "笔记B"):
        if data["score_a"].get(side, {}).get("分") not in _VALID_SCORES:
            logger.warning("维度 A %s 分数无效", side)
            return None

    # Validate score_b
    for side in ("笔记A", "笔记B"):
        if data["score_b"].get(side, {}).get("分") not in _VALID_SCORES:
            logger.warning("维度 B %s 分数无效", side)
            return None

    # Validate score_c
    for side in ("笔记A", "笔记B"):
        if data["score_c"].get(side, {}).get("分") not in _VALID_SCORES:
            logger.warning("维度 C %s 分数无效", side)
            return None

    # Validate score_d
    pref = data["score_d"].get("偏好")
    if pref not in _VALID_PREFERENCES:
        logger.warning("维度 D 偏好值无效: %s", pref)
        return None

    return data
