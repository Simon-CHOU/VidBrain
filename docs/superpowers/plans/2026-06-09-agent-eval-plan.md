# Agent EVAL System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fully automated A/B blind evaluation system that compares main-branch Agent output against embedding-branch (RAG-enhanced) Agent output on the same videos, using LLM-as-judge across 4 scoring dimensions.

**Architecture:** Three standalone scripts in an `eval/` directory that import from `src/` — `reviewer_prompt.py` (LLM prompt templates and JSON parsing), `eval_runner.py` (orchestrates parallel pipeline runs with isolated vaults), `aggregator.py` (statistical summary and decision). No modifications to existing `src/` code.

**Tech Stack:** Python 3.10+, openai (existing), pytest (existing), shutil (stdlib), json (stdlib)

---

## File Structure Map

| File | Action | Responsibility |
|------|--------|----------------|
| `eval/__init__.py` | **Create** | Package marker |
| `eval/reviewer_prompt.py` | **Create** | System prompt + user message template + JSON parsing |
| `eval/aggregator.py` | **Create** | Statistical aggregation + decision logic |
| `eval/eval_runner.py` | **Create** | Main orchestration: vault isolation, dual pipeline run, Reviewer invocation |
| `eval/video_list.txt` | **Create** | Placeholder for test video paths |
| `tests/test_reviewer_prompt.py` | **Create** | Tests for prompt rendering and JSON parsing |
| `tests/test_aggregator.py` | **Create** | Tests for aggregation logic and decision rules |

---

### Task 1: Reviewer Prompt Templates

**Files:**
- Create: `eval/__init__.py`
- Create: `eval/reviewer_prompt.py`
- Create: `tests/test_reviewer_prompt.py`

- [ ] **Step 1: Write the test file**

Create `tests/test_reviewer_prompt.py`:

```python
"""Tests for reviewer_prompt — prompt rendering and JSON parsing."""
from __future__ import annotations

import json
import pytest
from eval.reviewer_prompt import (
    build_system_prompt,
    build_user_message,
    parse_review_response,
    REVIEW_SYSTEM_PROMPT,
)


class TestBuildSystemPrompt:
    def test_returns_string(self):
        prompt = build_system_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 100

    def test_contains_all_four_dimensions(self):
        prompt = build_system_prompt()
        assert "术语纠错" in prompt
        assert "双链相关性" in prompt
        assert "更新建议" in prompt
        assert "综合质量" in prompt

    def test_contains_blind_rules(self):
        prompt = build_system_prompt()
        assert "笔记 A" in prompt
        assert "笔记 B" in prompt
        assert "不知道" in prompt or "身份" in prompt

    def test_contains_json_format_instruction(self):
        prompt = build_system_prompt()
        assert "JSON" in prompt


class TestBuildUserMessage:
    def test_renders_all_sections(self):
        msg = build_user_message(
            raw_text="raw asr text here",
            related_notes_summary="note1: preview",
            note_a_content="## Content A",
            note_a_links=["[[Python]]", "[[CUDA]]"],
            note_a_suggestions=[{"target_note": "Old", "type": "ref", "content": "see also"}],
            note_b_content="## Content B",
            note_b_links=["[[Python]]"],
            note_b_suggestions=[],
        )
        assert "raw asr text here" in msg
        assert "## Content A" in msg
        assert "## Content B" in msg
        assert "[[Python]]" in msg
        assert "[[CUDA]]" in msg
        assert "note1: preview" in msg

    def test_handles_empty_links(self):
        msg = build_user_message(
            raw_text="asr",
            related_notes_summary="",
            note_a_content="content A",
            note_a_links=[],
            note_a_suggestions=[],
            note_b_content="content B",
            note_b_links=[],
            note_b_suggestions=[],
        )
        assert "无双链" in msg or "[]" in msg

    def test_handles_empty_suggestions(self):
        msg = build_user_message(
            raw_text="asr",
            related_notes_summary="",
            note_a_content="A",
            note_a_links=[],
            note_a_suggestions=[],
            note_b_content="B",
            note_b_links=[],
            note_b_suggestions=[],
        )
        assert "无更新建议" in msg or "[]" in msg


VALID_REVIEW_JSON = """
{
  "asr_issues": ["术语问题1", "术语问题2"],
  "score_a": {
    "笔记A": {"分": 3, "证据": ["原文片段a1", "原文片段a2"]},
    "笔记B": {"分": 4, "证据": ["原文片段b1", "原文片段b2"]}
  },
  "score_b": {
    "笔记A": {"分": 3, "证据": [{"链接": "[[CUDA]]", "判断": "对", "理由": "上下文匹配"}]},
    "笔记B": {"分": 4, "证据": [{"链接": "[[GPU]]", "判断": "漏", "理由": "提到了但没链"}]}
  },
  "score_c": {
    "笔记A": {"分": 3, "证据": ["建议合理"]},
    "笔记B": {"分": 3, "证据": ["无更新建议"]}
  },
  "score_d": {"偏好": "B", "理由": "笔记B的术语更准确"},
  "self_doubt": {"最可能出偏误的维度": "C", "原因": "更新建议差异小"}
}
"""


class TestParseReviewResponse:
    def test_parses_valid_json(self):
        result = parse_review_response(VALID_REVIEW_JSON)
        assert result is not None
        assert result["score_a"]["笔记A"]["分"] == 3
        assert result["score_a"]["笔记B"]["分"] == 4
        assert result["score_d"]["偏好"] == "B"

    def test_parses_json_with_markdown_fence(self):
        wrapped = f"```json\n{VALID_REVIEW_JSON}\n```"
        result = parse_review_response(wrapped)
        assert result is not None
        assert result["score_d"]["偏好"] == "B"

    def test_rejects_invalid_json(self):
        result = parse_review_response("not valid json at all")
        assert result is None

    def test_rejects_missing_dimensions(self):
        incomplete = '{"score_a": {}, "score_b": {}}'
        result = parse_review_response(incomplete)
        assert result is None

    def test_rejects_out_of_range_scores(self):
        bad = json.loads(VALID_REVIEW_JSON)
        bad["score_a"]["笔记A"]["分"] = 7
        result = parse_review_response(json.dumps(bad))
        assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

```
python -m pytest tests/test_reviewer_prompt.py -v
```

Expected: ModuleNotFoundError for `eval.reviewer_prompt`.

- [ ] **Step 3: Write the reviewer_prompt module**

Create `eval/__init__.py` (empty file).

Create `eval/reviewer_prompt.py`:

```python
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
```

- [ ] **Step 4: Run tests**

```
python -m pytest tests/test_reviewer_prompt.py -v
```

Expected: All 10 tests pass.

- [ ] **Step 5: Commit**

```bash
git add eval/__init__.py eval/reviewer_prompt.py tests/test_reviewer_prompt.py
git commit -m "feat: add reviewer prompt templates and JSON validation for Agent EVAL"
```

---

### Task 2: Aggregator

**Files:**
- Create: `eval/aggregator.py`
- Create: `tests/test_aggregator.py`

- [ ] **Step 1: Write the test file**

Create `tests/test_aggregator.py`:

```python
"""Tests for aggregator — statistical summary and decision logic."""
from __future__ import annotations

from eval.aggregator import (
    compute_diff,
    aggregate_results,
    decide_merge,
    EvalResult,
    VideoPairResult,
)


def make_review(a_score: int, b_score: int, preference: str) -> dict:
    """Helper to build a minimal valid review dict."""
    return {
        "asr_issues": ["test issue"],
        "score_a": {
            "笔记A": {"分": a_score, "证据": ["e1"]},
            "笔记B": {"分": a_score, "证据": ["e2"]},
        },
        "score_b": {
            "笔记A": {"分": b_score, "证据": [{"链接": "[[X]]", "判断": "对", "理由": "ok"}]},
            "笔记B": {"分": b_score, "证据": [{"链接": "[[X]]", "判断": "对", "理由": "ok"}]},
        },
        "score_c": {
            "笔记A": {"分": 3, "证据": ["ok"]},
            "笔记B": {"分": 3, "证据": ["ok"]},
        },
        "score_d": {"偏好": preference, "理由": "test"},
        "self_doubt": {"最可能出偏误的维度": "C", "原因": "test"},
    }


class TestComputeDiff:
    def test_emb_wins_all_equal(self):
        """When A (emb) scores higher than B (main), diff is positive."""
        result = VideoPairResult(
            video_name="test.mp4",
            is_new_domain=True,
            review_a_is_emb=True,
            review=make_review(4, 4, "A"),
        )
        diffs = result.compute_diffs()
        # A score_b=4, B score_b=4, no diff on B. A score_a=4, B=4, no diff. A won D.
        assert diffs["A"] == 0  # scores equal
        assert diffs["B"] == 0
        assert diffs["D"] == "emb_win"

    def test_main_wins_when_b_scores_higher(self):
        result = VideoPairResult(
            video_name="test.mp4",
            is_new_domain=True,
            review_a_is_emb=False,  # A is main, B is emb
            review=make_review(3, 3, "B"),  # B (emb) wins D
        )
        diffs = result.compute_diffs()
        assert diffs["D"] == "emb_win"  # B is emb, won D

    def test_tie(self):
        result = VideoPairResult(
            video_name="test.mp4",
            is_new_domain=False,
            review_a_is_emb=True,
            review=make_review(3, 3, "持平"),
        )
        diffs = result.compute_diffs()
        assert diffs["D"] == "tie"


class TestAggregateResults:
    def test_empty_results(self):
        summary = aggregate_results([])
        assert summary["total_pairs"] == 0
        assert summary["conclusion"] == "insufficient_data"

    def test_emb_clean_sweep(self):
        results = []
        for i in range(10):
            is_new = i < 5
            results.append(VideoPairResult(
                video_name=f"v{i}.mp4",
                is_new_domain=is_new,
                review_a_is_emb=True,
                review=make_review(5, 5, "A"),
            ))
        summary = aggregate_results(results)
        assert summary["total_pairs"] == 10
        assert summary["dim_D_emb_win_pct"] == 1.0
        assert summary["conclusion"] == "merge"


class TestDecideMerge:
    def test_merge_when_all_conditions_met(self):
        summary = {
            "total_pairs": 20,
            "dim_A_avg_diff": 1.0,
            "dim_B_avg_diff": 0.5,
            "dim_C_avg_diff": 0.3,
            "dim_D_emb_win_pct": 0.65,
            "dim_D_emb_win_pct_existing": 0.7,
            "dim_D_emb_win_pct_new": 0.5,
            "self_doubt_flags": {},
        }
        conclusion = decide_merge(summary)
        assert conclusion == "merge"

    def test_no_merge_when_low_win_rate(self):
        summary = {
            "total_pairs": 20,
            "dim_A_avg_diff": 0.1,
            "dim_B_avg_diff": -0.2,
            "dim_C_avg_diff": 0.0,
            "dim_D_emb_win_pct": 0.3,
            "dim_D_emb_win_pct_existing": 0.3,
            "dim_D_emb_win_pct_new": 0.3,
            "self_doubt_flags": {},
        }
        conclusion = decide_merge(summary)
        assert conclusion == "no_merge"

    def test_rag_gain_unclear(self):
        summary = {
            "total_pairs": 20,
            "dim_A_avg_diff": 0.5,
            "dim_B_avg_diff": 0.3,
            "dim_C_avg_diff": 0.2,
            "dim_D_emb_win_pct": 0.65,
            "dim_D_emb_win_pct_existing": 0.4,
            "dim_D_emb_win_pct_new": 0.6,
            "self_doubt_flags": {},
        }
        conclusion = decide_merge(summary)
        assert conclusion == "rag_gain_unclear"

    def test_insufficient_data(self):
        conclusion = decide_merge({"total_pairs": 3})
        assert conclusion == "insufficient_data"
```

- [ ] **Step 2: Run tests to verify they fail**

```
python -m pytest tests/test_aggregator.py -v
```

Expected: ModuleNotFoundError for `eval.aggregator`.

- [ ] **Step 3: Write the aggregator module**

Create `eval/aggregator.py`:

```python
"""
Aggregation and decision logic for Agent EVAL results.

Reads per-pair review JSONs, computes statistical summaries,
and decides whether embedding-branch should be merged.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("vidbrain.eval")


@dataclass
class VideoPairResult:
    """A single A/B comparison result for one video pair."""
    video_name: str
    is_new_domain: bool          # True if vault has no related notes for this video
    review_a_is_emb: bool        # True if "笔记A" in the review is the embedding branch output
    review: dict                 # Parsed reviewer JSON

    def compute_diffs(self) -> dict:
        """Compute per-dimension diffs (emb - main) for this pair."""
        r = self.review
        if self.review_a_is_emb:
            emb_side = "笔记A"
            main_side = "笔记B"
        else:
            emb_side = "笔记B"
            main_side = "笔记A"

        a_diff = r["score_a"][emb_side]["分"] - r["score_a"][main_side]["分"]
        b_diff = r["score_b"][emb_side]["分"] - r["score_b"][main_side]["分"]
        c_diff = r["score_c"][emb_side]["分"] - r["score_c"][main_side]["分"]

        pref = r["score_d"]["偏好"]
        if pref == emb_side:
            d_result = "emb_win"
        elif pref == main_side:
            d_result = "main_win"
        else:
            d_result = "tie"

        return {
            "A": a_diff,
            "B": b_diff,
            "C": c_diff,
            "D": d_result,
            "video_name": self.video_name,
            "is_new_domain": self.is_new_domain,
        }


def aggregate_results(pairs: list[VideoPairResult]) -> dict:
    """Compute summary statistics across all video pairs.

    Returns a dict with keys suitable for decide_merge().
    """
    n = len(pairs)
    if n == 0:
        return {"total_pairs": 0, "conclusion": "insufficient_data"}

    diffs = [p.compute_diffs() for p in pairs]

    # Per-dimension averages
    a_avg = sum(d["A"] for d in diffs) / n
    b_avg = sum(d["B"] for d in diffs) / n
    c_avg = sum(d["C"] for d in diffs) / n

    # Dimension D win rates
    d_new = [d for d in diffs if d["is_new_domain"]]
    d_existing = [d for d in diffs if not d["is_new_domain"]]

    def _win_pct(d_list: list[dict]) -> float:
        if not d_list:
            return 0.5  # neutral if no data
        wins = sum(1 for d in d_list if d["D"] == "emb_win")
        return wins / len(d_list)

    d_all_pct = _win_pct(diffs)
    d_new_pct = _win_pct(d_new)
    d_existing_pct = _win_pct(d_existing)

    # Self-doubt analysis
    doubt_counts: dict[str, int] = {}
    for p in pairs:
        dim = p.review.get("self_doubt", {}).get("最可能出偏误的维度", "")
        if dim:
            doubt_counts[dim] = doubt_counts.get(dim, 0) + 1

    doubt_flags = {}
    for dim, count in doubt_counts.items():
        if count / n > 0.3:
            doubt_flags[dim] = {"count": count, "pct": count / n, "weight": 0.7}

    return {
        "total_pairs": n,
        "new_domain_pairs": len(d_new),
        "existing_domain_pairs": len(d_existing),
        "dim_A_avg_diff": round(a_avg, 2),
        "dim_B_avg_diff": round(b_avg, 2),
        "dim_C_avg_diff": round(c_avg, 2),
        "dim_D_emb_win_pct": round(d_all_pct, 2),
        "dim_D_emb_win_pct_new": round(d_new_pct, 2),
        "dim_D_emb_win_pct_existing": round(d_existing_pct, 2),
        "self_doubt_flags": doubt_flags,
    }


def decide_merge(summary: dict) -> str:
    """Apply decision rules to the summary and return a conclusion.

    Returns one of: "merge", "no_merge", "rag_gain_unclear", "insufficient_data"
    """
    n = summary.get("total_pairs", 0)
    if n < 10:
        return "insufficient_data"

    d_win = summary.get("dim_D_emb_win_pct", 0)
    d_existing = summary.get("dim_D_emb_win_pct_existing", 0)
    d_new = summary.get("dim_D_emb_win_pct_new", 0)

    # Condition 1: A+B+C+D combined win rate >= 60%
    # (approximated by D win rate, since D synthesizes A+B+C)
    cond1 = d_win >= 0.60

    # Condition 2: D dimension emb win rate >= 55%
    cond2 = d_win >= 0.55

    # Condition 3: existing domain gain >= new domain gain
    cond3 = d_existing >= d_new

    if cond1 and cond2 and cond3:
        return "merge"
    elif cond1 and cond2 and not cond3:
        return "rag_gain_unclear"
    else:
        return "no_merge"
```

- [ ] **Step 4: Run tests**

```
python -m pytest tests/test_aggregator.py -v
```

Expected: All 8 tests pass.

- [ ] **Step 5: Commit**

```bash
git add eval/aggregator.py tests/test_aggregator.py
git commit -m "feat: add aggregator with statistical summary and merge decision logic"
```

---

### Task 3: Eval Runner

**Files:**
- Create: `eval/eval_runner.py`
- Create: `eval/video_list.txt`

- [ ] **Step 1: Write eval_runner.py**

Create `eval/eval_runner.py`:

```python
#!/usr/bin/env python3
"""
Agent EVAL Runner — A/B blind comparison of main vs embedding-branch Agent.

Usage:
  python eval/eval_runner.py --seed-vault <path> --video-list <path> --output-dir <path>

For each video in the list:
  1. Creates isolated vaults (main_vault, emb_vault) copied from seed vault
  2. Runs main-branch pipeline (no embedding) on one, embedding-branch on the other
  3. Extracts outputs, calls LLM Reviewer for blind comparison
  4. Saves per-pair review JSON
After all pairs: runs aggregator to produce summary.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

# Add project root to sys.path so src imports work
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from openai import OpenAI

from src.models.config import EmbeddingConfig, LLMConfig, PipelineConfig
from src.services.asr_service import ASREngine
from src.services.pipeline_service import process_pipeline
from src.utils.db import DatabaseManager
from eval.reviewer_prompt import build_system_prompt, build_user_message, parse_review_response
from eval.aggregator import VideoPairResult, aggregate_results, decide_merge

logger = logging.getLogger("vidbrain.eval")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")


def _copy_vault(src: str, dst: str) -> None:
    """Copy vault directory contents from src to dst."""
    src_path = Path(src)
    dst_path = Path(dst)
    dst_path.mkdir(parents=True, exist_ok=True)
    if src_path.exists():
        for item in src_path.iterdir():
            s = src_path / item.name
            d = dst_path / item.name
            if item.is_dir():
                shutil.copytree(s, d)
            else:
                shutil.copy2(s, d)


def _extract_links(markdown: str) -> list[str]:
    """Extract [[wikilinks]] from markdown content."""
    import re
    return re.findall(r"\[\[([^\]]+)\]\]", markdown)


def _extract_markdown_without_frontmatter(full_content: str) -> str:
    """Remove YAML front-matter from note content."""
    if full_content.startswith("---"):
        end = full_content.find("---", 3)
        if end != -1:
            return full_content[end + 3:].strip()
    return full_content


def _call_reviewer(
    client: OpenAI,
    model: str,
    system_prompt: str,
    user_message: str,
) -> dict | None:
    """Call the LLM Reviewer and return parsed JSON, or None on failure."""
    for attempt in range(1, 3):
        temperature = 0.1 if attempt == 1 else 0.0
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                temperature=temperature,
                timeout=120,
            )
            raw = response.choices[0].message.content or ""
            result = parse_review_response(raw)
            if result is not None:
                return result
            logger.warning("Reviewer 返回无效 JSON (尝试 %d/2)", attempt)
        except Exception as e:
            logger.warning("Reviewer API 调用失败 (尝试 %d/2): %s", attempt, str(e))
            if attempt < 2:
                time.sleep(2)
    return None


def run_single_pipeline(
    video_path: str,
    vault_dir: str,
    db_path: str,
    llm_config: LLMConfig,
    asr_engine: ASREngine,
    embedding_enabled: bool = False,
) -> Path | None:
    """Run pipeline for one video, return the output .md file path or None."""
    video_name = Path(video_path).name
    video_id = Path(video_path).stem

    db = DatabaseManager(db_path)
    # Register the video in DB
    db.upsert_video(video_id, video_name, str(video_path))

    cfg = PipelineConfig(
        input_dir=str(Path(video_path).parent),
        vault_dir=vault_dir,
        db_path=db_path,
        model_size="tiny",
        cpu_threads=max(1, os.cpu_count() - 2) if os.cpu_count() else 2,
        batch_size=1,
        once=True,
        embedding_enabled=embedding_enabled,
    )

    emb_config = None
    if embedding_enabled:
        try:
            emb_config = EmbeddingConfig()
        except OSError as e:
            logger.warning("Embedding 配置失败，跳过: %s", str(e))
            return None

    try:
        process_pipeline(
            video_id=video_id,
            video_name=video_name,
            file_path=str(video_path),
            db=db,
            asr_engine=asr_engine,
            llm_config=llm_config,
            cfg=cfg,
            embedding_config=emb_config,
        )
    except Exception as e:
        logger.error("管道失败 (%s): %s", video_name, str(e))
        return None

    # Find the output note
    output_stem = Path(video_name).stem
    output_path = Path(vault_dir) / f"{output_stem}.md"
    if output_path.exists():
        return output_path
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent EVAL Runner")
    parser.add_argument("--seed-vault", required=True, help="Seed vault directory (baseline knowledge)")
    parser.add_argument("--video-list", required=True, help="File with video paths, one per line")
    parser.add_argument("--output-dir", default="./eval_results", help="Output directory for results")
    parser.add_argument("--asr-model", default="tiny", help="Whisper model size")
    parser.add_argument("--reviewer-model", default=None, help="LLM model for reviewing (default: same as LLM)")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    reviews_dir = output_dir / "reviews"
    reviews_dir.mkdir(exist_ok=True)

    # Read video list
    with open(args.video_list, encoding="utf-8") as f:
        videos = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    if not videos:
        logger.error("视频列表为空")
        sys.exit(1)

    # Init shared services
    llm_config = LLMConfig()
    reviewer_model = args.reviewer_model or llm_config.model
    reviewer_client = OpenAI(api_key=llm_config.api_key, base_url=llm_config.base_url)
    asr_engine = ASREngine(model_size=args.asr_model, cpu_threads=max(1, (os.cpu_count() or 4) - 2))

    results: list[VideoPairResult] = []
    skipped: list[dict] = []

    for i, video_path in enumerate(videos):
        logger.info("=== 视频 %d/%d: %s ===", i + 1, len(videos), video_path)

        if not Path(video_path).exists():
            logger.warning("跳过不存在的文件: %s", video_path)
            skipped.append({"video": video_path, "reason": "file_not_found"})
            continue

        # Create isolated vaults
        work_dir = Path(tempfile.mkdtemp(prefix="eval_"))
        main_vault = work_dir / "main_vault"
        emb_vault = work_dir / "emb_vault"
        main_db = work_dir / "main.db"
        emb_db = work_dir / "emb.db"

        try:
            _copy_vault(args.seed_vault, str(main_vault))
            _copy_vault(args.seed_vault, str(emb_vault))

            # Randomize which branch runs first
            emb_first = random.choice([True, False])

            if emb_first:
                emb_output = run_single_pipeline(
                    video_path, str(emb_vault), str(emb_db),
                    llm_config, asr_engine, embedding_enabled=True,
                )
                main_output = run_single_pipeline(
                    video_path, str(main_vault), str(main_db),
                    llm_config, asr_engine, embedding_enabled=False,
                )
            else:
                main_output = run_single_pipeline(
                    video_path, str(main_vault), str(main_db),
                    llm_config, asr_engine, embedding_enabled=False,
                )
                emb_output = run_single_pipeline(
                    video_path, str(emb_vault), str(emb_db),
                    llm_config, asr_engine, embedding_enabled=True,
                )

            if main_output is None or emb_output is None:
                logger.warning("跳过: 管道输出缺失 (main=%s, emb=%s)",
                               main_output is not None, emb_output is not None)
                skipped.append({"video": video_path, "reason": "pipeline_failure"})
                continue

            # Read outputs
            main_full = main_output.read_text(encoding="utf-8", errors="replace")
            emb_full = emb_output.read_text(encoding="utf-8", errors="replace")
            main_md = _extract_markdown_without_frontmatter(main_full)
            emb_md = _extract_markdown_without_frontmatter(emb_full)
            main_links = _extract_links(main_md)
            emb_links = _extract_links(emb_md)

            # Determine if new domain (vault had no related notes)
            existing_notes = list(Path(args.seed_vault).glob("*.md")) if Path(args.seed_vault).exists() else []
            is_new_domain = len(existing_notes) == 0

            # Randomize A/B labels for blind review
            emb_is_a = random.choice([True, False])
            if emb_is_a:
                note_a_content = emb_md
                note_a_links = emb_links
                note_b_content = main_md
                note_b_links = main_links
            else:
                note_a_content = main_md
                note_a_links = main_links
                note_b_content = emb_md
                note_b_links = emb_links

            # Build reviewer message (no update suggestions in this MVP — extracted from pipeline)
            user_msg = build_user_message(
                raw_text="(ASR text not separately archived — see note content)",
                related_notes_summary="",
                note_a_content=note_a_content[:5000],
                note_a_links=note_a_links,
                note_a_suggestions=[],
                note_b_content=note_b_content[:5000],
                note_b_links=note_b_links,
                note_b_suggestions=[],
            )

            review = _call_reviewer(
                reviewer_client, reviewer_model,
                build_system_prompt(), user_msg,
            )

            if review is None:
                logger.warning("评审失败: %s", video_path)
                skipped.append({"video": video_path, "reason": "review_failure"})
                continue

            # Save review
            review_path = reviews_dir / f"{Path(video_path).stem}_review.json"
            review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")

            pair_result = VideoPairResult(
                video_name=Path(video_path).name,
                is_new_domain=is_new_domain,
                review_a_is_emb=emb_is_a,
                review=review,
            )
            results.append(pair_result)
            logger.info("评审完成: %s", Path(video_path).name)

        finally:
            # Cleanup temp vaults
            shutil.rmtree(work_dir, ignore_errors=True)

    # Aggregation
    summary = aggregate_results(results)
    conclusion = decide_merge(summary)
    summary["conclusion"] = conclusion
    summary["skipped_count"] = len(skipped)
    summary["skipped"] = skipped
    summary["generated_at"] = datetime.now().isoformat()

    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info("=== EVAL 完成 ===")
    logger.info("处理: %d, 跳过: %d, 结论: %s", len(results), len(skipped), conclusion)
    logger.info("汇总: %s", summary_path)

    # Print key stats
    print(f"\n{'='*60}")
    print(f"Agent EVAL 结果")
    print(f"{'='*60}")
    print(f"总视频对: {summary['total_pairs']}")
    print(f"维度 A (术语) 平均分差: {summary['dim_A_avg_diff']:+.2f}")
    print(f"维度 B (双链) 平均分差: {summary['dim_B_avg_diff']:+.2f}")
    print(f"维度 C (更新) 平均分差: {summary['dim_C_avg_diff']:+.2f}")
    print(f"维度 D (综合) emb 胜出: {summary['dim_D_emb_win_pct']:.0%}")
    print(f"  已有领域: {summary['dim_D_emb_win_pct_existing']:.0%}")
    print(f"  新领域:   {summary['dim_D_emb_win_pct_new']:.0%}")
    if summary.get("self_doubt_flags"):
        print(f"低置信维度: {list(summary['self_doubt_flags'].keys())}")
    print(f"\n结论: {conclusion}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Create video_list.txt placeholder**

Create `eval/video_list.txt`:

```
# Agent EVAL 测试视频列表
# 每行一个视频文件路径（mp4）
# 以 # 开头的行为注释
#
# 示例：
# I:\web-videos\tech\BV1xx1234xxx.mp4
# I:\web-videos\ai\BV1yy5678yyy.mp4
```

- [ ] **Step 3: Verify imports work**

```
python -c "from eval.reviewer_prompt import build_system_prompt, parse_review_response; from eval.aggregator import aggregate_results, decide_merge; print('Imports OK')"
```

Expected: `Imports OK`

- [ ] **Step 4: Run all tests**

```
python -m pytest tests/test_reviewer_prompt.py tests/test_aggregator.py -v
```

Expected: All 18 tests pass.

- [ ] **Step 5: Commit**

```bash
git add eval/eval_runner.py eval/video_list.txt
git commit -m "feat: add eval_runner — automated A/B blind comparison pipeline"
```

---

### Task 4: Integration Smoke Test

**Files:**
- Create: (no new files — manual verification)

- [ ] **Step 1: Verify project-level imports and full test suite**

```
python -m pytest tests/ -v --tb=short
```

Expected: All existing 176 tests pass + 18 new eval tests = 194 total.

- [ ] **Step 2: Run ruff lint on eval/**

```
python -m ruff check eval/
```

Fix any issues found.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "test: verify full test suite with eval modules (194 tests)"
```
