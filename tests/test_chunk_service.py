"""Tests for chunk_service — chunk splitting and ChunkStore."""
from __future__ import annotations

import numpy as np
import pytest
from unittest.mock import MagicMock

from src.services.chunk_service import ChunkStore, chunk_note_content


class TestChunkNoteContent:
    """Tests for chunk_note_content()."""

    def test_splits_by_h2_headings(self):
        content = (
            "## Intro\n" + "content. " * 30 + "\n\n"
            "## Main\n" + "content. " * 30
        )
        chunks = chunk_note_content(content)
        assert len(chunks) == 2
        assert chunks[0]["content"].startswith("## Intro")
        assert chunks[1]["content"].startswith("## Main")

    def test_splits_by_h3_in_long_sections(self):
        intro = "intro paragraph. " * 50
        detail_a = "### Detail A\n" + "detail a content. " * 10 + "\n\n"
        detail_b = "### Detail B\n" + "detail b content. " * 10
        content = f"## Overview\n{intro}\n\n{detail_a}{detail_b}"
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
            "## Actual Content\n" + "real content. " * 20
        )
        chunks = chunk_note_content(content)
        assert "---" not in chunks[0]["content"]
        assert "type:" not in chunks[0]["content"]

    def test_content_preserved_in_order(self):
        content = (
            "## First\n" + "first content. " * 20 + "\n\n"
            "## Second\n" + "second content. " * 20 + "\n\n"
            "## Third\n" + "third content. " * 20
        )
        chunks = chunk_note_content(content)
        assert len(chunks) == 3
        full = "".join(c["content"] for c in chunks)
        assert "first content" in full
        assert "third content" in full

    def test_token_count_is_estimated(self):
        content = "## Test\nThis is test content with about thirty characters."
        chunks = chunk_note_content(content)
        assert "token_count" in chunks[0]
        assert isinstance(chunks[0]["token_count"], int)
        assert chunks[0]["token_count"] > 0

    def test_empty_content_returns_singleton(self):
        chunks = chunk_note_content("")
        assert len(chunks) == 1
        assert chunks[0]["content"] == "(empty)"
        assert chunks[0]["token_count"] == 1


class TestChunkStore:
    """Tests for ChunkStore."""

    @pytest.fixture
    def mock_engine(self):
        engine = MagicMock()
        engine.embed_batch.side_effect = lambda texts: [[0.1] * 1024 for _ in texts]
        engine.embed.return_value = [0.1] * 1024
        return engine

    def make_store(self, vault_path: str) -> ChunkStore:
        return ChunkStore(vault_path)

    def test_init_creates_db_and_npy(self, tmp_path):
        store = self.make_store(str(tmp_path))
        assert (tmp_path / ".vidbrain_chunks.db").exists()
        assert (tmp_path / ".vidbrain_chunk_vectors.npy").exists()
        vecs = np.load(str(tmp_path / ".vidbrain_chunk_vectors.npy"))
        assert vecs.shape == (0, 1024)

    def test_chunk_note_stores_metadata(self, tmp_path, mock_engine):
        store = self.make_store(str(tmp_path))
        content = "## Intro\nThis is content about machine learning.\n\n## Details\nDeep dive into attention mechanisms."
        store.chunk_note("TestNote", content, mock_engine)
        assert "TestNote" in store.get_all_note_names()

    def test_find_similar_returns_chunks(self, tmp_path, mock_engine):
        store = self.make_store(str(tmp_path))
        content = "## Section A\nThe quick brown fox jumps over the lazy dog.\n\n## Section B\nMachine learning is transforming technology."
        store.chunk_note("NoteA", content, mock_engine)
        results = store.find_similar([0.1] * 1024, top_k=3)
        assert len(results) >= 1
        for r in results:
            assert hasattr(r, "content")
            assert hasattr(r, "similarity")
            assert hasattr(r, "note_name")

    def test_find_similar_includes_neighbor_context(self, tmp_path, mock_engine):
        store = self.make_store(str(tmp_path))
        content = "## One\nFirst chunk with enough text to meet minimum size requirements here.\n\n## Two\nSecond chunk also with enough text to meet the minimum size threshold for testing.\n\n## Three\nThird chunk that has enough content to be a proper chunk with minimum length satisfied."
        store.chunk_note("NeighborNote", content, mock_engine)
        results = store.find_similar([0.1] * 1024, top_k=3)
        if len(results) > 1:
            ctx = results[1].full_context()
            assert results[1].content in ctx

    def test_is_stale_detects_mtime_change(self, tmp_path, mock_engine):
        store = self.make_store(str(tmp_path))
        note_path = tmp_path / "StaleTest.md"
        note_path.write_text(
            "## Test\nEnough content here to make a valid chunk with proper sizing.",
            encoding="utf-8",
        )
        content = note_path.read_text(encoding="utf-8")
        store.chunk_note("StaleTest", content, mock_engine)
        mtime = note_path.stat().st_mtime
        assert not store.is_stale("StaleTest", mtime)
        assert store.is_stale("StaleTest", mtime + 100.0)

    def test_remove_note_cleans_up(self, tmp_path, mock_engine):
        store = self.make_store(str(tmp_path))
        content = "## A\nFirst chunk with sufficient content here.\n\n## B\nSecond chunk also with enough text to be meaningful."
        store.chunk_note("RemoveTest", content, mock_engine)
        assert "RemoveTest" in store.get_all_note_names()
        store.remove_note("RemoveTest")
        assert "RemoveTest" not in store.get_all_note_names()

    def test_get_unchunked_notes_finds_new_notes(self, tmp_path, mock_engine):
        store = self.make_store(str(tmp_path))
        (tmp_path / "Unchunked.md").write_text(
            "## Content\nSome content here with enough text for a valid chunk.",
            encoding="utf-8",
        )
        (tmp_path / "AlsoUnchunked.md").write_text("Minimal note.", encoding="utf-8")
        unchunked = store.get_unchunked_notes()
        assert len(unchunked) >= 2
        names = [n for n, _ in unchunked]
        assert "Unchunked" in names

    def test_get_unchunked_notes_excludes_indexed(self, tmp_path, mock_engine):
        store = self.make_store(str(tmp_path))
        note_path = tmp_path / "Indexed.md"
        note_path.write_text(
            "## Content\nSome content here with enough text for a valid chunk test.",
            encoding="utf-8",
        )
        store.chunk_note("Indexed", note_path.read_text(encoding="utf-8"), mock_engine)
        (tmp_path / "NotIndexed.md").write_text(
            "## Content\nDifferent content with sufficient length to be meaningful.",
            encoding="utf-8",
        )
        unchunked = store.get_unchunked_notes()
        names = [n for n, _ in unchunked]
        assert "Indexed" not in names
        assert "NotIndexed" in names

    def test_chunk_id_sequence_in_note(self, tmp_path, mock_engine):
        store = self.make_store(str(tmp_path))
        content = (
            "## First\n" + "First chunk with enough content to be a proper chunk. " * 8 + "\n\n"
            "## Second\n" + "Second chunk also with sufficient content for the test. " * 8 + "\n\n"
            "## Third\n" + "Third chunk with more content that meets minimum length. " * 8 + "\n\n"
            "## Fourth\n" + "Fourth chunk with another piece of content for the test. " * 8
        )
        store.chunk_note("SeqTest", content, mock_engine)
        results = store.find_similar([0.1] * 1024, top_k=10)
        note_chunks = [r for r in results if r.note_name == "SeqTest"]
        assert len(note_chunks) >= 2
        for c in note_chunks:
            assert c.chunk_id.startswith("SeqTest#")
