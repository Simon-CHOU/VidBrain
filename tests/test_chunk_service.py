"""Tests for chunk_service — chunk splitting and ChunkStore."""
from __future__ import annotations

import pytest
from src.services.chunk_service import chunk_note_content


class TestChunkNoteContent:
    """Tests for chunk_note_content()."""

    def test_splits_by_h2_headings(self):
        content = "## Intro\nThis is intro content.\n\n## Main\nThis is main content."
        chunks = chunk_note_content(content)
        assert len(chunks) == 2
        assert chunks[0]["content"].startswith("## Intro")
        assert chunks[1]["content"].startswith("## Main")

    def test_splits_by_h3_in_long_sections(self):
        intro = "intro paragraph. " * 50
        content = f"## Overview\n{intro}\n\n### Detail A\nShort detail.\n\n### Detail B\nMore detail."
        chunks = chunk_note_content(content)
        assert len(chunks) >= 3

    def test_merges_tiny_chunks(self):
        content = "## A\nab\n\n## B\ncd\n\n## C\nA proper paragraph with enough characters to stand alone as a meaningful chunk."
        chunks = chunk_note_content(content)
        assert len(chunks) < 3

    def test_short_note_stays_single_chunk(self):
        content = "This is a very short note without headings."
        chunks = chunk_note_content(content)
        assert len(chunks) == 1
        assert "This is a very short note" in chunks[0]["content"]

    def test_strips_frontmatter(self):
        content = (
            "---\ntype: note\nstatus: auto\n---\n\n"
            "## Actual Content\nThis is the real content."
        )
        chunks = chunk_note_content(content)
        assert "---" not in chunks[0]["content"]
        assert "type:" not in chunks[0]["content"]

    def test_content_preserved_in_order(self):
        content = "## First\nFirst content.\n\n## Second\nSecond content.\n\n## Third\nThird content."
        chunks = chunk_note_content(content)
        assert len(chunks) == 3
        full = "".join(c["content"] for c in chunks)
        assert "First content" in full
        assert "Third content" in full

    def test_token_count_is_estimated(self):
        content = "## Test\nThis is test content with about thirty characters."
        chunks = chunk_note_content(content)
        assert "token_count" in chunks[0]
        assert isinstance(chunks[0]["token_count"], int)
        assert chunks[0]["token_count"] > 0
