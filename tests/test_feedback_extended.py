"""Extended feedback service tests."""

from __future__ import annotations

from pathlib import Path

from src.services.feedback_service import detect_user_edits, extract_feedback_signals


class TestDetectUserEdits:
    def test_detects_user_edited_note(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        note = vault / "edited.md"
        note.write_text(
            "---\nstatus: auto-generated\ncreated: 2020-01-01 00:00:00\n---\nBody",
            encoding="utf-8",
        )
        results = detect_user_edits(str(vault))
        assert any(r["name"] == "edited" and r["user_edited"] for r in results)

    def test_detects_reviewed_note(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "rev.md").write_text(
            "---\nstatus: auto-generated\nreviewed: true\n---\nBody",
            encoding="utf-8",
        )
        results = detect_user_edits(str(vault))
        assert any(r["name"] == "rev" and r["reviewed"] for r in results)


class TestExtractFeedbackSignals:
    def test_extracts_from_edited_notes(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "n1.md").write_text(
            "---\nstatus: auto-generated\ncreated: 2020-01-01 00:00:00\n---\nPrefers [[Python]] links.",
            encoding="utf-8",
        )
        (vault / "n2.md").write_text(
            "---\nstatus: auto-generated\nreviewed: true\n---\nGood note.",
            encoding="utf-8",
        )
        edited = detect_user_edits(str(vault))
        signals = extract_feedback_signals(str(vault), edited)
        assert signals["edited_count"] >= 1
        assert signals["reviewed_count"] >= 1
