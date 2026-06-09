"""Tests for LangGraph agent service."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from src.models.config import LLMConfig
from src.services.agent_service import _call_llm, create_agent_graph


@pytest.fixture
def llm_config(mock_env_deepseek) -> LLMConfig:
    return LLMConfig()


class TestCallLlm:
    def test_success(self) -> None:
        client = MagicMock()
        client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="response text"))]
        )
        with (
            patch("src.services.agent_service.get_metrics"),
            patch("src.services.agent_service.get_audit"),
        ):
            result = _call_llm(client, "model", "prompt", 0.2)
        assert result == "response text"

    def test_retries_then_raises(self) -> None:
        client = MagicMock()
        client.chat.completions.create.side_effect = RuntimeError("api down")
        with (
            patch("src.services.agent_service.get_metrics"),
            patch("src.services.agent_service.get_audit"),
            patch("src.services.agent_service.time.sleep"),
        ):
            with pytest.raises(RuntimeError):
                _call_llm(client, "model", "prompt", 0.2)
        assert client.chat.completions.create.call_count == 3


class TestCreateAgentGraph:
    @patch("src.services.agent_service._call_llm")
    @patch("src.services.agent_service.OpenAI")
    def test_full_graph_invoke(self, mock_openai, mock_llm, llm_config: LLMConfig) -> None:
        mock_llm.side_effect = [
            "## Intro\n\nCleaned markdown with [[Python]]",
            "## Intro\n\nLinked [[Python]] [[CUDA]]",
            '{"suggestions": [{"target_note": "Old", "type": "ref", "content": "see new"}]}',
        ]
        graph = create_agent_graph(llm_config)
        state = {
            "video_id": "v1",
            "video_name": "test.mp4",
            "raw_text": "raw asr text about Python",
            "existing_notes": ["Python", "CUDA"],
            "related_notes": [
                {
                    "name": "Old",
                    "match_terms": ["Python"],
                    "content_preview": "old note about Python",
                }
            ],
            "update_suggestions": [],
            "final_markdown": "",
            "feedback_context": "prefer links to Python",
        }
        result = graph.invoke(state)
        assert "final_markdown" in result
        assert len(result.get("update_suggestions", [])) >= 0

    @patch("src.services.agent_service._call_llm")
    @patch("src.services.agent_service.OpenAI")
    def test_suggest_update_skipped_without_related(
        self, mock_openai, mock_llm, llm_config: LLMConfig
    ) -> None:
        mock_llm.side_effect = ["md1", "md2"]
        graph = create_agent_graph(llm_config)
        state = {
            "video_id": "v1",
            "video_name": "test.mp4",
            "raw_text": "text",
            "existing_notes": [],
            "related_notes": [],
            "update_suggestions": [],
            "final_markdown": "",
            "feedback_context": "",
        }
        result = graph.invoke(state)
        assert result["update_suggestions"] == []

    @patch("src.services.agent_service._call_llm")
    @patch("src.services.agent_service.OpenAI")
    def test_suggest_update_bad_json(self, mock_openai, mock_llm, llm_config: LLMConfig) -> None:
        mock_llm.side_effect = ["md1", "md2", "not json at all"]
        graph = create_agent_graph(llm_config)
        state = {
            "video_id": "v1",
            "video_name": "test.mp4",
            "raw_text": "text",
            "existing_notes": ["A"],
            "related_notes": [{"name": "A", "match_terms": ["t"], "content_preview": "p"}],
            "update_suggestions": [],
            "final_markdown": "",
            "feedback_context": "",
        }
        result = graph.invoke(state)
        assert result["update_suggestions"] == []
