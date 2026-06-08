"""Tests for user feedback detection and extraction."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.services.feedback_service import (
    detect_user_edits,
    extract_feedback_signals,
    get_feedback_context,
    parse_front_matter,
)


class TestParseFrontMatter:
    """Tests for parse_front_matter function."""

    def test_empty_content(self) -> None:
        """Should return empty dict for empty content."""
        assert parse_front_matter("") == {}

    def test_no_front_matter(self) -> None:
        """Should return empty dict when no front-matter delimiters."""
        assert parse_front_matter("# Title\n\nContent") == {}

    def test_missing_closing_delimiter(self) -> None:
        """Should return empty dict when closing --- is missing."""
        assert parse_front_matter("---\ntype: note\n") == {}

    def test_basic_fields(self) -> None:
        """Should parse basic YAML key-value pairs."""
        content = "---\ntype: technical-note\nstatus: auto-generated\n---\n\nContent"
        result = parse_front_matter(content)
        assert result["type"] == "technical-note"
        assert result["status"] == "auto-generated"

    def test_boolean_true(self) -> None:
        """Should convert 'true' to Python True."""
        content = "---\nreviewed: true\n---\n\nContent"
        result = parse_front_matter(content)
        assert result["reviewed"] is True

    def test_boolean_false(self) -> None:
        """Should convert 'false' to Python False."""
        content = "---\nreviewed: false\n---\n\nContent"
        result = parse_front_matter(content)
        assert result["reviewed"] is False

    def test_created_field(self) -> None:
        """Should parse created timestamp."""
        content = "---\ncreated: 2025-01-15 10:30:00\n---\n\nContent"
        result = parse_front_matter(content)
        assert result["created"] == "2025-01-15 10:30:00"

    def test_multiple_fields(self) -> None:
        """Should parse multiple fields correctly."""
        content = (
            "---\n"
            "type: technical-note\n"
            "status: auto-generated\n"
            "quality_score: 8\n"
            "reviewed: true\n"
            "source_video: test.mp4\n"
            "---\n\n"
            "Content"
        )
        result = parse_front_matter(content)
        assert result["type"] == "technical-note"
        assert result["quality_score"] == "8"
        assert result["reviewed"] is True

    def test_extra_spaces(self) -> None:
        """Should handle extra whitespace around values."""
        content = "---\ntype:   technical-note  \n---\n\nContent"
        result = parse_front_matter(content)
        assert result["type"] == "technical-note"


class TestDetectUserEdits:
    """Tests for detect_user_edits function."""

    def test_empty_vault(self, temp_dir: Path) -> None:
        """Should return empty list for non-existent directory."""
        result = detect_user_edits(str(temp_dir / "nonexistent"))
        assert result == []

    def test_no_md_files(self, temp_dir: Path) -> None:
        """Should return empty list when no .md files exist."""
        temp_dir.mkdir(parents=True, exist_ok=True)
        (temp_dir / "notes.txt").write_text("hello")
        result = detect_user_edits(str(temp_dir))
        assert result == []


class TestExtractFeedbackSignals:
    """Tests for extract_feedback_signals function."""

    def test_empty_edited_notes(self, temp_dir: Path) -> None:
        """Should return default dict with zeros when no edited notes."""
        result = extract_feedback_signals(str(temp_dir), [])
        assert result["preferred_links"] == []
        assert result["edited_count"] == 0
        assert result["reviewed_count"] == 0


class TestGetFeedbackContext:
    """Tests for get_feedback_context function."""

    def test_empty_signals(self) -> None:
        """Should return empty string for empty signals."""
        result = get_feedback_context(
            {
                "preferred_links": [],
                "avoid_links": [],
                "edited_count": 0,
                "reviewed_count": 0,
            }
        )
        assert result == ""

    def test_with_preferred_links(self) -> None:
        """Should include preferred links in context."""
        result = get_feedback_context(
            {
                "preferred_links": ["Python", "CUDA"],
                "avoid_links": [],
                "edited_count": 3,
                "reviewed_count": 1,
            }
        )
        assert "[[Python]]" in result
        assert "[[CUDA]]" in result
        assert "3 篇" in result

    def test_with_edited_count(self) -> None:
        """Should mention edited count."""
        result = get_feedback_context(
            {
                "preferred_links": [],
                "avoid_links": [],
                "edited_count": 5,
                "reviewed_count": 0,
            }
        )
        assert "5 篇笔记" in result

    def test_with_reviewed_count(self) -> None:
        """Should mention reviewed count."""
        result = get_feedback_context(
            {
                "preferred_links": [],
                "avoid_links": [],
                "edited_count": 0,
                "reviewed_count": 7,
            }
        )
        assert "7 篇笔记" in result
