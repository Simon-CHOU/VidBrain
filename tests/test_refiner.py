"""Tests for vault refiner module."""

from __future__ import annotations

from typing import Any

from src.services.refiner_service import (
    analyze_links,
    parse_links,
    read_note,
    scan_vault,
)


class TestParseLinks:
    """Tests for parse_links function."""

    def test_no_links(self) -> None:
        """Should return empty list when no wiki links present."""
        assert parse_links("Just some text without links.") == []

    def test_single_link(self) -> None:
        """Should extract a single wiki link."""
        assert parse_links("See [[Python]] for details.") == ["Python"]

    def test_multiple_links(self) -> None:
        """Should extract multiple wiki links."""
        text = "Learn [[Python]], [[CUDA]], and [[Machine Learning]]."
        result = parse_links(text)
        assert result == ["Python", "CUDA", "Machine Learning"]

    def test_link_with_alias(self) -> None:
        """Should extract link target ignoring alias."""
        assert parse_links("See [[Python|Python Language]].") == ["Python"]

    def test_empty_string(self) -> None:
        """Should handle empty string."""
        assert parse_links("") == []

    def test_duplicate_links(self) -> None:
        """Should preserve duplicates (raw extraction)."""
        text = "[[AI]] and [[AI]] again."
        assert parse_links(text) == ["AI", "AI"]

    def test_links_with_whitespace(self) -> None:
        """Should strip whitespace from link targets."""
        assert parse_links("[[  Python  ]]") == ["Python"]


class TestAnalyzeLinks:
    """Tests for analyze_links function."""

    def test_empty_notes(self) -> None:
        """Should handle empty notes list."""
        result = analyze_links([])
        assert result["orphan_no_outgoing"] == []
        assert result["orphan_no_incoming"] == []

    def test_single_note_no_links(self) -> None:
        """Single note with no links is orphan for both."""
        notes: list[dict[str, Any]] = [
            {"name": "Note1", "outgoing_links": [], "content": "", "path": "/n1.md"}
        ]
        result = analyze_links(notes)
        assert len(result["orphan_no_outgoing"]) == 1
        assert len(result["orphan_no_incoming"]) == 1

    def test_two_linked_notes(self) -> None:
        """Two notes referencing each other should have no orphans."""
        notes: list[dict[str, Any]] = [
            {
                "name": "Note1",
                "outgoing_links": ["Note2"],
                "content": "",
                "path": "/n1.md",
            },
            {
                "name": "Note2",
                "outgoing_links": ["Note1"],
                "content": "",
                "path": "/n2.md",
            },
        ]
        result = analyze_links(notes)
        assert result["orphan_no_outgoing"] == []
        assert result["orphan_no_incoming"] == []

    def test_one_way_link(self) -> None:
        """Note with incoming link but no outgoing is not orphan_no_incoming."""
        notes: list[dict[str, Any]] = [
            {
                "name": "Note1",
                "outgoing_links": ["Note2"],
                "content": "",
                "path": "/n1.md",
            },
            {"name": "Note2", "outgoing_links": [], "content": "", "path": "/n2.md"},
        ]
        result = analyze_links(notes)
        assert len(result["orphan_no_outgoing"]) == 1  # Note2
        assert len(result["orphan_no_incoming"]) == 1  # Note1 (no one links to it)

    def test_outgoing_counts(self) -> None:
        """Should correctly count outgoing links."""
        notes: list[dict[str, Any]] = [
            {
                "name": "Note1",
                "outgoing_links": ["A", "B", "C"],
                "content": "",
                "path": "/n1.md",
            },
        ]
        result = analyze_links(notes)
        assert result["outgoing_counts"]["Note1"] == 3

    def test_incoming_counts(self) -> None:
        """Should correctly count incoming links."""
        notes: list[dict[str, Any]] = [
            {
                "name": "Note1",
                "outgoing_links": ["Target"],
                "content": "",
                "path": "/n1.md",
            },
            {
                "name": "Note2",
                "outgoing_links": ["Target"],
                "content": "",
                "path": "/n2.md",
            },
            {"name": "Target", "outgoing_links": [], "content": "", "path": "/t.md"},
        ]
        result = analyze_links(notes)
        assert result["incoming_counts"]["Target"] == 2


class TestReadNote:
    def test_read_note_extracts_links_and_quality(self, tmp_path) -> None:
        note = tmp_path / "note.md"
        note.write_text(
            "---\nquality_score: 6\n---\nSee [[Python]] and [[CUDA]].",
            encoding="utf-8",
        )
        data = read_note(note)
        assert data["name"] == "note"
        assert data["quality"] == 6
        assert "Python" in data["outgoing_links"]


class TestScanVault:
    def test_scan_vault_skips_moc(self, tmp_path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "note.md").write_text("[[A]]", encoding="utf-8")
        (vault / "MOC-index.md").write_text("moc", encoding="utf-8")
        notes = scan_vault(str(vault))
        assert len(notes) == 1
        assert notes[0]["name"] == "note"

    def test_scan_vault_missing_dir(self, tmp_path) -> None:
        assert scan_vault(str(tmp_path / "missing")) == []
