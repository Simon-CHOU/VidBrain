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
