"""Tests for pipeline service."""

from __future__ import annotations

from src.services.pipeline_service import _compute_quality_score


class TestComputeQualityScore:
    """Tests for _compute_quality_score function."""

    def test_minimal_content(self) -> None:
        """Should give low score for minimal content."""
        score = _compute_quality_score(
            asr_segments=3,
            final_markdown="Just some text.",
        )
        assert score >= 0
        assert score <= 5  # Low score expected

    def test_rich_content(self) -> None:
        """Should give higher score for well-structured content with links."""
        content = (
            "## Introduction\n\n"
            "Content about [[Python]] and [[Machine Learning]].\n\n"
            "## Details\n\n"
            "More info with [[CUDA]] reference.\n"
        )
        score = _compute_quality_score(
            asr_segments=35,
            final_markdown=content,
        )
        assert score >= 5  # Higher score due to segments + headings + links

    def test_user_edited_bonus(self) -> None:
        """Should add bonus for user-edited content."""
        score_without = _compute_quality_score(
            10, "## Title\n\nContent.", user_edited=False
        )
        score_with = _compute_quality_score(
            10, "## Title\n\nContent.", user_edited=True
        )
        assert score_with > score_without

    def test_reviewed_bonus(self) -> None:
        """Should add bonus for reviewed content."""
        score_without = _compute_quality_score(
            10, "## Title\n\nContent.", reviewed=False
        )
        score_with = _compute_quality_score(10, "## Title\n\nContent.", reviewed=True)
        assert score_with > score_without

    def test_score_capped_at_10(self) -> None:
        """Should cap score at 10 maximum."""
        content = "## Intro\n## Body\n## Conclusion\n" "[[A]] [[B]] [[C]] [[D]] [[E]]\n"
        score = _compute_quality_score(
            asr_segments=50,
            final_markdown=content,
            user_edited=True,
            reviewed=True,
        )
        assert score <= 10

    def test_zero_segments(self) -> None:
        """Zero segments should still compute valid score."""
        score = _compute_quality_score(0, "## Title\n\n[[Link]]")
        assert score >= 0

    def test_empty_markdown(self) -> None:
        """Empty markdown should give base score."""
        score = _compute_quality_score(20, "")
        assert score == 2  # 2 for segments count
