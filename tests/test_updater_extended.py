"""Extended updater service tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.services.updater_service import check_and_update, check_related_notes


class TestCheckRelatedNotes:
    def test_substring_match(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "python.md").write_text("# Python\n\nCore language.", encoding="utf-8")
        related = check_related_notes(
            str(vault),
            "new_video.mp4",
            "## Python\n\nThis video discusses `Python` programming.",
            ["python"],
        )
        assert len(related) >= 1
        assert related[0]["stem"] == "python"

    @patch("src.services.updater_service._check_related_embedding")
    def test_embedding_path(self, mock_embed, tmp_path: Path) -> None:
        mock_embed.return_value = [
            {"name": "a", "stem": "a", "match_terms": ["sim"], "content_preview": "p"}
        ]
        related = check_related_notes(
            str(tmp_path),
            "v.mp4",
            "text",
            ["a"],
            embedding_enabled=True,
            embedding_store=MagicMock(),
            embedding_engine=MagicMock(),
        )
        assert related[0]["stem"] == "a"


class TestCheckAndUpdate:
    @patch("src.services.updater_service.apply_update", return_value=True)
    @patch("src.services.updater_service.suggest_update")
    def test_check_and_update_applies(
        self, mock_suggest, mock_apply, tmp_path: Path, mock_env_deepseek
    ) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "target.md").write_text("# Target\n\nOld.", encoding="utf-8")
        mock_suggest.return_value = [
            {"target_note": "target", "type": "ref", "content": "see also new"}
        ]
        from src.models.config import LLMConfig

        with patch(
            "src.services.updater_service.check_related_notes",
            return_value=[
                {
                    "name": "target",
                    "stem": "target",
                    "match_terms": ["Target"],
                    "content_preview": "Old.",
                }
            ],
        ):
            count = check_and_update(
                str(vault),
                "new.mp4",
                "## New\n\nDiscusses `Target` topic.",
                ["target"],
                LLMConfig(),
            )
        assert count == 1
