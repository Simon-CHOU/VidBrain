"""单元测试：Draft Manager 模块"""

import tempfile
from pathlib import Path

from vidbrain.drafts import write_draft, list_drafts, publish_draft, discard_draft


class TestWriteDraft:
    def test_write_draft_creates_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_draft(tmp, "test_note.md", "## Hello World", "source.mp4")
            draft_dir = Path(tmp) / "_drafts"
            assert draft_dir.exists()
            draft_file = draft_dir / "test_note.md"
            assert draft_file.exists()
            content = draft_file.read_text(encoding="utf-8")
            assert "status: draft" in content
            assert "source_video: source.mp4" in content
            assert "## Hello World" in content

    def test_write_draft_replaces_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent_output = "---\nold: frontmatter\n---\n\n## Real Content\n"
            write_draft(tmp, "note.md", agent_output, "vid.mp4")
            draft_file = Path(tmp) / "_drafts" / "note.md"
            content = draft_file.read_text(encoding="utf-8")
            assert "status: draft" in content
            assert "old: frontmatter" not in content
            assert "## Real Content" in content


class TestListDrafts:
    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            assert list_drafts(tmp) == []

    def test_lists_drafts_sorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_draft(tmp, "B.md", "b", "v.mp4")
            write_draft(tmp, "A.md", "a", "v.mp4")
            drafts = list_drafts(tmp)
            assert drafts == ["A.md", "B.md"]


class TestPublishDraft:
    def test_publish_moves_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_draft(tmp, "note.md", "## Content", "src.mp4")
            result = publish_draft(tmp, "note.md")
            assert result is not None
            # 应该在 Vault 根目录
            assert result == Path(tmp) / "note.md"
            assert result.exists()
            # _drafts/ 下不应再有
            assert not (Path(tmp) / "_drafts" / "note.md").exists()

    def test_publish_updates_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_draft(tmp, "note.md", "## Content", "src.mp4")
            publish_draft(tmp, "note.md")
            content = (Path(tmp) / "note.md").read_text(encoding="utf-8")
            assert "status: auto-generated" in content
            assert "reviewed: true" in content
            assert "reviewed_at:" in content

    def test_publish_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            assert publish_draft(tmp, "nonexistent.md") is None


class TestDiscardDraft:
    def test_discard_removes_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_draft(tmp, "note.md", "## Content", "src.mp4")
            assert discard_draft(tmp, "note.md") is True
            assert not (Path(tmp) / "_drafts" / "note.md").exists()

    def test_discard_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            assert discard_draft(tmp, "nonexistent.md") is False
