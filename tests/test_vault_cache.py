"""Tests for vault note cache."""

from __future__ import annotations

from pathlib import Path

import pytest
from src.utils.frontmatter import read_quality_score, strip_frontmatter
from src.utils.vault_cache import (
    VaultCache,
    _read_preview_from_disk,
    get_vault_cache,
)


class TestVaultCacheHelpers:
    def test_read_quality_from_front_matter(self) -> None:
        content = "---\nquality_score: 8\n---\nbody"
        assert read_quality_score(content) == 8

    def test_read_quality_default(self) -> None:
        assert read_quality_score("no front matter") == 0

    def test_strip_front_matter(self) -> None:
        body = strip_frontmatter("---\ntype: note\n---\n# Title\n\nHello")
        assert body.startswith("# Title")

    def test_read_preview_from_disk(self, tmp_path: Path) -> None:
        note = tmp_path / "note.md"
        note.write_text("---\nquality_score: 5\n---\nPreview text", encoding="utf-8")
        assert "Preview" in _read_preview_from_disk(str(tmp_path), "note")


class TestVaultCache:
    @pytest.fixture(autouse=True)
    def reset_global_cache(self) -> None:
        import src.utils.vault_cache as vc

        vc._vault_cache = None
        yield
        vc._vault_cache = None

    def test_get_existing_notes_sorted(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "low.md").write_text(
            "---\nquality_score: 1\n---\n[[A]]", encoding="utf-8"
        )
        (vault / "high.md").write_text(
            "---\nquality_score: 9\n---\n[[B]]", encoding="utf-8"
        )
        cache = VaultCache()
        stems = cache.get_existing_notes(str(vault))
        assert stems[0] == "high"

    def test_skips_drafts_subdirectory(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        drafts = vault / "_drafts"
        drafts.mkdir(parents=True)
        (drafts / "draft.md").write_text("draft", encoding="utf-8")
        (vault / "real.md").write_text("---\nquality_score: 3\n---\n", encoding="utf-8")
        cache = VaultCache()
        stems = cache.get_existing_notes(str(vault))
        assert stems == ["real"]

    def test_invalidate_and_rescan(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "a.md").write_text("---\nquality_score: 1\n---\n", encoding="utf-8")
        cache = VaultCache()
        cache.get_existing_notes(str(vault))
        cache.invalidate()
        (vault / "b.md").write_text("---\nquality_score: 2\n---\n", encoding="utf-8")
        stems = cache.get_existing_notes(str(vault))
        assert "a" in stems and "b" in stems

    def test_get_content_preview_from_cache(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "note.md").write_text(
            "---\nquality_score: 4\n---\nCached preview body",
            encoding="utf-8",
        )
        cache = VaultCache()
        cache.get_existing_notes(str(vault))
        preview = cache.get_content_preview("note", vault_path=str(vault))
        assert "Cached preview" in preview

    def test_add_note_incremental(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        cache = VaultCache()
        cache.get_existing_notes(str(vault))
        cache.add_note(
            str(vault),
            "newnote",
            "---\nquality_score: 7\n---\nNew content",
        )
        preview = cache.get_content_preview("newnote")
        assert "New content" in preview

    def test_get_vault_cache_singleton(self) -> None:
        assert get_vault_cache() is get_vault_cache()

    def test_nonexistent_vault_returns_empty(self, tmp_path: Path) -> None:
        cache = VaultCache()
        assert cache.get_existing_notes(str(tmp_path / "missing")) == []

    def test_detects_file_change_after_cooldown(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "a.md").write_text("---\nquality_score: 1\n---\n", encoding="utf-8")
        cache = VaultCache()
        cache._scan_cooldown = 0.0
        cache.get_existing_notes(str(vault))
        cache._last_full_scan = 0.0
        (vault / "b.md").write_text("---\nquality_score: 2\n---\n", encoding="utf-8")
        stems = cache.get_existing_notes(str(vault))
        assert "b" in stems
