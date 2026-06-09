"""Tests for reviewer_prompt."""
from __future__ import annotations
import json
import pytest
from eval.reviewer_prompt import build_system_prompt, build_user_message, parse_review_response

VALID_REVIEW_JSON = """
{
  "asr_issues": ["术语问题1", "术语问题2"],
  "score_a": {
    "笔记A": {"分": 3, "证据": ["原文片段a1"]},
    "笔记B": {"分": 4, "证据": ["原文片段b1"]}
  },
  "score_b": {
    "笔记A": {"分": 3, "证据": [{"链接": "[[CUDA]]", "判断": "对", "理由": "ok"}]},
    "笔记B": {"分": 4, "证据": [{"链接": "[[GPU]]", "判断": "漏", "理由": "missed"}]}
  },
  "score_c": {
    "笔记A": {"分": 3, "证据": ["ok"]},
    "笔记B": {"分": 3, "证据": ["ok"]}
  },
  "score_d": {"偏好": "B", "理由": "B的术语更准"},
  "self_doubt": {"最可能出偏误的维度": "C", "原因": "差异小"}
}
"""

class TestBuildSystemPrompt:
    def test_returns_non_empty_string(self):
        p = build_system_prompt()
        assert isinstance(p, str)
        assert len(p) > 100

    def test_contains_four_dimensions(self):
        p = build_system_prompt()
        assert "术语纠错" in p
        assert "双链相关性" in p
        assert "更新建议" in p
        assert "综合质量" in p

    def test_contains_blind_rules(self):
        p = build_system_prompt()
        assert "笔记 A" in p
        assert "不知道" in p or "身份" in p

    def test_requires_json_output(self):
        p = build_system_prompt()
        assert "JSON" in p


class TestBuildUserMessage:
    def test_renders_all_sections(self):
        msg = build_user_message(
            raw_text="raw asr",
            related_notes_summary="note1: preview",
            note_a_content="## Content A",
            note_a_links=["[[Python]]"],
            note_a_suggestions=[{"target_note": "Old", "type": "ref", "content": "see"}],
            note_b_content="## Content B",
            note_b_links=[],
            note_b_suggestions=[],
        )
        assert "raw asr" in msg
        assert "## Content A" in msg
        assert "## Content B" in msg
        assert "[[Python]]" in msg
        assert "note1: preview" in msg

    def test_empty_links_shows_placeholder(self):
        msg = build_user_message("asr", "", "A", [], [], "B", [], [])
        assert "（无双链）" in msg

    def test_empty_suggestions_shows_placeholder(self):
        msg = build_user_message("asr", "", "A", [], [], "B", [], [])
        assert "（无更新建议）" in msg


class TestParseReviewResponse:
    def test_parses_valid_json(self):
        r = parse_review_response(VALID_REVIEW_JSON)
        assert r is not None
        assert r["score_a"]["笔记A"]["分"] == 3
        assert r["score_d"]["偏好"] == "B"

    def test_strips_markdown_fence(self):
        r = parse_review_response(f"```json\n{VALID_REVIEW_JSON}\n```")
        assert r is not None

    def test_rejects_invalid_json(self):
        assert parse_review_response("not json") is None

    def test_rejects_missing_keys(self):
        assert parse_review_response('{"score_a": {}}') is None

    def test_rejects_bad_score(self):
        bad = json.loads(VALID_REVIEW_JSON)
        bad["score_a"]["笔记A"]["分"] = 7
        assert parse_review_response(json.dumps(bad)) is None
