"""Extended refiner service tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.services.refiner_service import (
    _call_llm_batch,
    apply_suggestions,
    generate_moc_files,
    refine_vault,
)


class TestApplySuggestions:
    def test_applies_new_links(self, tmp_path: Path) -> None:
        note = tmp_path / "a.md"
        note.write_text("# Note A\n\nContent about Python.", encoding="utf-8")
        notes = [
            {
                "name": "a",
                "content": "# Note A\n\nContent about Python.",
                "outgoing_links": [],
                "path": str(note),
            }
        ]
        suggestions = [{"note": "a", "links": ["b", "c"]}]
        applied = apply_suggestions(str(tmp_path), suggestions, notes)
        assert applied == 1
        text = note.read_text(encoding="utf-8")
        assert "[[b]]" in text and "[[c]]" in text

    def test_skips_existing_links(self, tmp_path: Path) -> None:
        note = tmp_path / "a.md"
        note.write_text("[[b]]", encoding="utf-8")
        notes = [
            {
                "name": "a",
                "content": "[[b]]",
                "outgoing_links": ["b"],
                "path": str(note),
            }
        ]
        applied = apply_suggestions(str(tmp_path), [{"note": "a", "links": ["b"]}], notes)
        assert applied == 0


class TestCallLlmBatch:
    def test_parses_json_response(self) -> None:
        client = MagicMock()
        client.chat.completions.create.return_value = MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(
                        content='{"suggestions": [{"note": "a", "links": ["b"]}]}'
                    )
                )
            ]
        )
        notes = [{"name": "a", "content": "text about b"}]
        result = _call_llm_batch(client, "model", notes, ["a", "b"])
        assert result[0]["note"] == "a"

    def test_returns_empty_on_failure(self) -> None:
        client = MagicMock()
        client.chat.completions.create.side_effect = RuntimeError("fail")
        with patch("src.services.refiner_service.time.sleep"):
            result = _call_llm_batch(client, "model", [{"name": "a", "content": "x"}], ["a"])
        assert result == []


class TestGenerateMocFiles:
    def test_creates_moc_file(self, tmp_path: Path) -> None:
        topics = [
            {
                "topic": "Python",
                "notes": ["intro", "advanced"],
                "description": "Python notes",
            }
        ]
        created = generate_moc_files(str(tmp_path), topics)
        assert created == 1
        moc = tmp_path / "MOC-Python.md"
        assert moc.is_file()
        assert "[[intro]]" in moc.read_text(encoding="utf-8")

    def test_skips_existing_moc(self, tmp_path: Path) -> None:
        (tmp_path / "MOC-Python.md").write_text("existing", encoding="utf-8")
        created = generate_moc_files(
            str(tmp_path),
            [{"topic": "Python", "notes": ["a"], "description": ""}],
        )
        assert created == 0


class TestRefineVault:
    @patch("src.services.refiner_service.generate_moc_files", return_value=0)
    @patch("src.services.refiner_service._extract_topics", return_value=[])
    @patch("src.services.refiner_service.apply_suggestions", return_value=0)
    @patch("src.services.refiner_service._call_llm_batch", return_value=[])
    @patch("src.services.refiner_service.OpenAI")
    def test_refine_vault_with_notes(
        self, mock_openai, _batch, _apply, _topics, _moc, tmp_path: Path, mock_env_deepseek
    ) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "note.md").write_text("# Note\n\nStandalone.", encoding="utf-8")
        from src.models.config import LLMConfig

        refine_vault(str(vault), LLMConfig())
        _topics.assert_called_once()
