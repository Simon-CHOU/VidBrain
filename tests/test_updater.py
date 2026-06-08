"""Tests for incremental updater module."""

from __future__ import annotations

from typing import Any

from src.services.updater_service import _extract_key_terms, _match_notes, apply_update


class TestExtractKeyTerms:
    """Tests for _extract_key_terms function."""

    def test_backtick_terms(self) -> None:
        """Should extract terms from backtick-wrapped text."""
        content = "We use `CUDA` and `FlashAttention` for optimization."
        terms = _extract_key_terms(content)
        assert "CUDA" in terms
        assert "FlashAttention" in terms

    def test_heading_terms(self) -> None:
        """Should extract terms from markdown headings."""
        content = "## Machine Learning\n## DistributedSystems\nContent."
        terms = _extract_key_terms(content)
        assert any("Machine" in t for t in terms) or any("Learning" in t for t in terms)

    def test_deduplication(self) -> None:
        """Should remove duplicate terms."""
        content = "`CUDA` and `CUDA` again."
        terms = _extract_key_terms(content)
        assert terms.count("CUDA") == 1

    def test_short_terms_filtered(self) -> None:
        """Should filter out very short backtick terms."""
        content = "`a` `ab` `abc`"
        terms = _extract_key_terms(content)
        # Terms with length < 2 are filtered
        assert "a" not in terms


class TestMatchNotes:
    """Tests for _match_notes function."""

    def test_substring_match(self) -> None:
        """Should match terms against note stems via substring."""
        terms = ["Python", "CUDA"]
        existing = ["python-programming", "javascript-basics", "cuda-kernel"]
        result = _match_notes(terms, existing)
        matched_names = [r["name"] for r in result]
        assert "python-programming" in matched_names
        assert "cuda-kernel" in matched_names
        assert "javascript-basics" not in matched_names

    def test_case_insensitive_match(self) -> None:
        """Should match case-insensitively."""
        terms = ["Python"]
        existing = ["PYTHON-GUIDE"]
        result = _match_notes(terms, existing)
        assert len(result) == 1

    def test_empty_terms(self) -> None:
        """Should return empty list for empty terms."""
        assert _match_notes([], ["note1"]) == []

    def test_empty_existing(self) -> None:
        """Should return empty list for empty existing notes."""
        assert _match_notes(["Python"], []) == []

    def test_top_n_limit(self) -> None:
        """Should return at most top_n results."""
        terms = ["AI"]
        existing = ["AI-cuda", "AI-ml", "AI-dl", "AI-rl"]
        result = _match_notes(terms, existing, top_n=2)
        assert len(result) <= 2


class TestApplyUpdate:
    """Tests for apply_update function."""

    def test_invalid_suggestion(self, temp_dir: Any) -> None:
        """Should return False for invalid suggestion."""
        assert apply_update(str(temp_dir), {"target_note": "", "content": ""}) is False

    def test_nonexistent_target(self, temp_dir: Any) -> None:
        """Should return False when target note doesn't exist."""
        assert apply_update(str(temp_dir), {"target_note": "ghost", "content": "x"}) is False
