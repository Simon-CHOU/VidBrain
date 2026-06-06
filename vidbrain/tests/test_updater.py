"""单元测试：增量内容更新模块"""

import tempfile
from pathlib import Path

from vidbrain.updater import check_related_notes, apply_update


def _create_note(vault_path: str, name: str, content: str) -> None:
    """在 vault 中创建一篇笔记。"""
    fp = Path(vault_path) / f"{name}.md"
    fp.write_text(content, encoding="utf-8")


class TestCheckRelatedNotes:
    def test_no_terms(self):
        """无关键术语时返回空列表。"""
        with tempfile.TemporaryDirectory() as tmp:
            result = check_related_notes(
                tmp, "new_note", "abc 123 xyz", ["ExistingNote"],
            )
            assert result == []

    def test_backtick_term_match(self):
        """反引号术语匹配到已有笔记。"""
        with tempfile.TemporaryDirectory() as tmp:
            # 创建已有笔记
            _create_note(tmp, "FlashAttention", "## FlashAttention 是一种高效的注意力机制。")

            existing = ["FlashAttention", "UnrelatedNote"]
            result = check_related_notes(
                tmp, "new_note",
                "This note mentions `FlashAttention` as a key optimization technique.",
                existing,
            )

            assert len(result) >= 1
            matched_names = [r["name"] for r in result]
            assert "FlashAttention" in matched_names
            # 应包含 match_terms
            assert "FlashAttention" in result[0]["match_terms"]

    def test_heading_term_match(self):
        """## 标题中长度 > 4 的单词匹配到已有笔记。"""
        with tempfile.TemporaryDirectory() as tmp:
            _create_note(tmp, "FlashAttention", "FlashAttention 是一种高效的注意力机制。")

            existing = ["FlashAttention", "OtherNote"]
            result = check_related_notes(
                tmp, "new_note",
                "## FlashAttention\n\nThis section covers attention mechanisms.",
                existing,
            )

            assert len(result) >= 1
            matched_names = [r["name"] for r in result]
            assert "FlashAttention" in matched_names

    def test_multiple_matches_top3(self):
        """多个匹配时只返回 top 3。"""
        with tempfile.TemporaryDirectory() as tmp:
            for name in ["CUDA", "FlashAttention", "KvCache", "TensorCore"]:
                _create_note(tmp, name, f"# {name}\n\nContent about {name}.")

            existing = ["CUDA", "FlashAttention", "KvCache", "TensorCore"]
            result = check_related_notes(
                tmp, "attention_optimization",
                "## FlashAttention\n\nUses `CUDA` and `KvCache` and `TensorCore` optimizations.",
                existing,
            )

            assert len(result) <= 3
            # 应该包含至少部分匹配
            matched_names = [r["name"] for r in result]
            assert len(matched_names) > 0

    def test_content_preview(self):
        """关联笔记应包含 content_preview 字段。"""
        with tempfile.TemporaryDirectory() as tmp:
            _create_note(tmp, "FlashAttention", "---\ntype: note\n---\n\n" + ("x" * 350) + " final content.")

            existing = ["FlashAttention"]
            result = check_related_notes(
                tmp, "new_note",
                "## FlashAttention\n\nUsing `FlashAttention` optimization.",
                existing,
            )

            assert len(result) == 1
            assert "content_preview" in result[0]
            preview = result[0]["content_preview"]
            # front-matter 已跳过，内容不应超过 400 字符
            assert len(preview) <= 400
            assert "final content" in preview

    def test_no_matching_notes(self):
        """术语与已有笔记无匹配时返回空。"""
        with tempfile.TemporaryDirectory() as tmp:
            _create_note(tmp, "Unrelated", "Some unrelated content.")

            existing = ["Unrelated"]
            result = check_related_notes(
                tmp, "new_note",
                "## Python\n\n`Transformer` is a new architecture.",
                existing,
            )

            assert result == []

    def test_missing_note_file(self):
        """已有笔记 stem 存在但文件不存在时不应崩溃。"""
        with tempfile.TemporaryDirectory() as tmp:
            existing = ["GhostNote"]
            result = check_related_notes(
                tmp, "new_note",
                "## GhostNote\n\nTalking about `GhostNote`.",
                existing,
            )

            assert len(result) >= 1
            # 文件不存在时 content_preview 应为空
            assert result[0]["content_preview"] == ""


class TestApplyUpdate:
    def test_apply_update_appends_content(self):
        """应成功将更新块追加到笔记末尾。"""
        with tempfile.TemporaryDirectory() as tmp:
            original = "---\ntype: note\n---\n\n## Original Content\n\nThis is original."
            _create_note(tmp, "TargetNote", original)

            suggestion = {
                "target_note": "TargetNote",
                "new_note_name": "NewNote",
                "type": "ref",
                "content": "See also [[NewNote]] for related information.",
            }
            result = apply_update(tmp, suggestion)
            assert result is True

            updated = (Path(tmp) / "TargetNote.md").read_text(encoding="utf-8")
            assert "## Original Content" in updated
            assert "[[NewNote]]" in updated
            assert "*[自动更新: 关联笔记 [[NewNote]]]*" in updated
            assert "See also" in updated

    def test_apply_update_nonexistent_target(self):
        """目标笔记不存在时返回 False。"""
        with tempfile.TemporaryDirectory() as tmp:
            suggestion = {
                "target_note": "GhostNote",
                "content": "Should not be applied.",
            }
            result = apply_update(tmp, suggestion)
            assert result is False

    def test_apply_update_empty_content(self):
        """空 content 时返回 False。"""
        with tempfile.TemporaryDirectory() as tmp:
            _create_note(tmp, "Target", "# Target")
            suggestion = {
                "target_note": "Target",
                "content": "",
            }
            result = apply_update(tmp, suggestion)
            assert result is False

    def test_apply_update_no_target_note(self):
        """无 target_note 时返回 False。"""
        with tempfile.TemporaryDirectory() as tmp:
            suggestion = {
                "content": "Some content",
            }
            result = apply_update(tmp, suggestion)
            assert result is False
