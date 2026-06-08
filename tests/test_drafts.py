"""Tests for draft manager."""

from __future__ import annotations

from pathlib import Path

from src.services.drafts_service import (
    discard_draft,
    list_drafts,
    publish_draft,
    write_draft,
)


class TestWriteDraft:
    """Tests for write_draft function."""

    def test_creates_drafts_directory(self, temp_dir: Path) -> None:
        """Should create _drafts/ directory if it doesn't exist."""
        vault = str(temp_dir / "vault")
        write_draft(vault, "test_note.md", "Content here", "source.mp4")
        assert (Path(vault) / "_drafts").is_dir()

    def test_writes_content(self, temp_dir: Path) -> None:
        """Should write the draft content to a file."""
        vault = str(temp_dir / "vault")
        result = write_draft(vault, "note.md", "### Section\n\nBody", "video.mp4")
        assert result.exists()
        content = result.read_text(encoding="utf-8")
        assert "### Section" in content
        assert "video.mp4" in content

    def test_adds_front_matter(self, temp_dir: Path) -> None:
        """Should prepend front-matter to the draft."""
        vault = str(temp_dir / "vault")
        result = write_draft(vault, "note.md", "Body text", "src.mp4")
        content = result.read_text(encoding="utf-8")
        assert content.startswith("---")
        assert "type: technical-note" in content
        assert "status: draft" in content
        assert "source_video: src.mp4" in content

    def test_replaces_existing_front_matter(self, temp_dir: Path) -> None:
        """Should replace existing front-matter in content."""
        vault = str(temp_dir / "vault")
        content_with_fm = "---\ntype: old\n---\n\nReal content"
        result = write_draft(vault, "note.md", content_with_fm, "src.mp4")
        content = result.read_text(encoding="utf-8")
        assert "type: old" not in content
        assert "Real content" in content


class TestListDrafts:
    """Tests for list_drafts function."""

    def test_empty_when_no_drafts_dir(self, temp_dir: Path) -> None:
        """Should return empty list when _drafts/ doesn't exist."""
        vault = str(temp_dir / "vault")
        assert list_drafts(vault) == []

    def test_lists_draft_files(self, temp_dir: Path) -> None:
        """Should list all .md files in _drafts/."""
        vault = str(temp_dir / "vault")
        write_draft(vault, "a.md", "A", "s.mp4")
        write_draft(vault, "b.md", "B", "s.mp4")
        drafts = list_drafts(vault)
        assert len(drafts) == 2
        assert "a.md" in drafts
        assert "b.md" in drafts

    def test_sorted_order(self, temp_dir: Path) -> None:
        """Should return sorted list."""
        vault = str(temp_dir / "vault")
        write_draft(vault, "z.md", "Z", "s.mp4")
        write_draft(vault, "a.md", "A", "s.mp4")
        drafts = list_drafts(vault)
        assert drafts == ["a.md", "z.md"]


class TestPublishDraft:
    """Tests for publish_draft function."""

    def test_returns_none_for_nonexistent(self, temp_dir: Path) -> None:
        """Should return None when draft doesn't exist."""
        vault = str(temp_dir / "vault")
        assert publish_draft(vault, "nonexistent.md") is None

    def test_moves_to_vault_root(self, temp_dir: Path) -> None:
        """Should move draft from _drafts/ to vault root."""
        vault = str(temp_dir / "vault")
        write_draft(vault, "note.md", "Content", "src.mp4")
        result = publish_draft(vault, "note.md")
        assert result is not None
        assert result.parent == Path(vault)
        assert result.name == "note.md"
        # Original draft should be gone
        assert not (Path(vault) / "_drafts" / "note.md").exists()

    def test_updates_status_in_front_matter(self, temp_dir: Path) -> None:
        """Should change status from draft to auto-generated."""
        vault = str(temp_dir / "vault")
        write_draft(vault, "note.md", "Content", "src.mp4")
        result = publish_draft(vault, "note.md")
        content = result.read_text(encoding="utf-8") if result else ""
        assert "status: auto-generated" in content
        assert "reviewed: true" in content


class TestDiscardDraft:
    """Tests for discard_draft function."""

    def test_returns_false_for_nonexistent(self, temp_dir: Path) -> None:
        """Should return False when draft doesn't exist."""
        vault = str(temp_dir / "vault")
        assert discard_draft(vault, "nonexistent.md") is False

    def test_deletes_draft(self, temp_dir: Path) -> None:
        """Should delete the draft file."""
        vault = str(temp_dir / "vault")
        write_draft(vault, "note.md", "Content", "src.mp4")
        assert discard_draft(vault, "note.md") is True
        assert not (Path(vault) / "_drafts" / "note.md").exists()
