"""Tests for agent_service -- RAG context injection into three-node prompts."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.models.state import AgentState
from src.services.agent_service import create_agent_graph


class TestAgentStateRagContext:
    """Verify rag_context field exists in AgentState."""

    def test_rag_context_is_field(self) -> None:
        """rag_context should be a defined field in AgentState."""
        assert "rag_context" in AgentState.__annotations__

    def test_rag_context_defaults_to_empty(self) -> None:
        """An AgentState without rag_context should act as if empty."""
        state: AgentState = {
            "video_id": "test-123",
            "video_name": "test.mp4",
            "raw_text": "some asr text",
            "existing_notes": [],
            "related_notes": [],
            "update_suggestions": [],
            "final_markdown": "",
            "feedback_context": "",
        }
        # rag_context is optional (total=False), so .get() should return default
        assert state.get("rag_context", "") == ""


_BASE_STATE: dict[str, object] = {
    "video_id": "test-id",
    "video_name": "test.mp4",
    "raw_text": "some raw text",
    "existing_notes": [],
    "related_notes": [],
    "update_suggestions": [],
    "final_markdown": "",
    "feedback_context": "",
    "rag_context": "",
}


def _make_state(**overrides: object) -> AgentState:
    base = dict(_BASE_STATE)
    base.update(overrides)
    return AgentState(**base)  # type: ignore[arg-type]


@pytest.fixture
def llm_config(mock_env_deepseek: None) -> MagicMock:  # noqa: ARG001
    """Create a minimal LLMConfig."""
    from src.models.config import LLMConfig

    return LLMConfig()


# ---------------------------------------------------------------------------
# Helper: extract prompt from a specific call index in the pipeline
# ---------------------------------------------------------------------------
# The LangGraph pipeline calls nodes in order:
#   0 = clean_and_extract_node
#   1 = auto_link_node
#   2 = suggest_update_node


def _prompt_at(mock: MagicMock, index: int) -> str:
    """Return the prompt (3rd positional arg) of the *index*-th LLM call."""
    return mock.call_args_list[index][0][2]


# ===================================================================
# Node 1: clean_and_extract
# ===================================================================


class TestCleanAndExtractNode:
    """Verify clean_and_extract_node uses rag_context."""

    def test_includes_rag_in_prompt_when_present(self, llm_config: MagicMock) -> None:
        """When rag_context is non-empty, the prompt should include [系统参考]."""
        state = _make_state(rag_context="CUDA is a parallel computing platform")

        with patch("src.services.agent_service._call_llm", return_value="cleaned") as m:
            create_agent_graph(llm_config).invoke(state)

        prompt = _prompt_at(m, 0)
        assert "[系统参考]" in prompt
        assert "CUDA is a parallel computing platform" in prompt
        assert "知识库中已存在以下相关内容片段" in prompt

    def test_falls_back_when_rag_empty(self, llm_config: MagicMock) -> None:
        """When rag_context is empty, the RAG section should be absent."""
        state = _make_state(rag_context="")

        with patch("src.services.agent_service._call_llm", return_value="cleaned") as m:
            create_agent_graph(llm_config).invoke(state)

        prompt = _prompt_at(m, 0)
        assert "[系统参考]" not in prompt
        # The fallback prompt still mentions this example term
        assert "单子融合" in prompt

    def test_returns_final_markdown(self, llm_config: MagicMock) -> None:
        """The node should return final_markdown in the output dict."""
        state = _make_state()

        with patch("src.services.agent_service._call_llm", return_value="cleaned result"):
            result = create_agent_graph(llm_config).invoke(state)

        assert "final_markdown" in result


# ===================================================================
# Node 2: auto_link
# ===================================================================


class TestAutoLinkNode:
    """Verify auto_link_node uses rag_context."""

    def _state_with_md(self, **overrides: object) -> AgentState:
        return _make_state(
            existing_notes=["CUDA Notes", "Kafka Guide"],
            final_markdown="# Test Note\nSome content about CUDA.",
            **overrides,
        )

    def test_includes_rag_in_prompt_when_present(self, llm_config: MagicMock) -> None:
        """When rag_context is non-empty, the prompt should include [知识库检索结果]."""
        state = self._state_with_md(rag_context="CUDA programming model notes")

        with patch("src.services.agent_service._call_llm", return_value="linked") as m:
            create_agent_graph(llm_config).invoke(state)

        prompt = _prompt_at(m, 1)  # second call in pipeline
        assert "[知识库检索结果]" in prompt
        assert "CUDA programming model notes" in prompt
        assert "参考上方检索片段中反复出现的笔记名" in prompt

    def test_falls_back_when_rag_empty(self, llm_config: MagicMock) -> None:
        """When rag_context is empty, the RAG section should be absent."""
        state = self._state_with_md(rag_context="")

        with patch("src.services.agent_service._call_llm", return_value="linked") as m:
            create_agent_graph(llm_config).invoke(state)

        prompt = _prompt_at(m, 1)
        assert "[知识库检索结果]" not in prompt
        assert "参考上方检索片段中反复出现的笔记名" not in prompt


# ===================================================================
# Node 3: suggest_update
# ===================================================================


class TestSuggestUpdateNode:
    """Verify suggest_update_node uses rag_context."""

    @staticmethod
    def _related_note(name: str = "Note 1") -> list[dict]:
        return [
            {
                "name": name,
                "stem": name.lower().replace(" ", "-"),
                "match_terms": ["test"],
                "content_preview": "some content",
            }
        ]

    def test_includes_rag_in_prompt_when_present(self, llm_config: MagicMock) -> None:
        """When rag_context is non-empty, the prompt should include [Knowledge Base Retrieval]."""
        state = _make_state(
            related_notes=self._related_note(),
            rag_context="CUDA parallel computing platform",
            final_markdown="# Test Note\ncontent here " * 100,
        )

        with patch(
            "src.services.agent_service._call_llm",
            return_value='{"suggestions": []}',
        ) as m:
            create_agent_graph(llm_config).invoke(state)

        prompt = _prompt_at(m, 2)
        assert "[Knowledge Base Retrieval]" in prompt
        assert "semantically similar" in prompt
        assert "CUDA parallel computing platform" in prompt

    def test_rag_context_section_absent_when_empty(self, llm_config: MagicMock) -> None:
        """When rag_context is empty, [Knowledge Base Retrieval] should be absent."""
        state = _make_state(
            related_notes=self._related_note(),
            rag_context="",
            final_markdown="# Test Note\ncontent here " * 100,
        )

        with patch(
            "src.services.agent_service._call_llm",
            return_value='{"suggestions": []}',
        ) as m:
            create_agent_graph(llm_config).invoke(state)

        prompt = _prompt_at(m, 2)
        assert "[Knowledge Base Retrieval]" not in prompt
        assert "semantically similar" not in prompt

    def test_skips_when_no_related_and_no_rag(self, llm_config: MagicMock) -> None:
        """When both related_notes and rag_context are empty, suggest_update short-circuits.

        The first two nodes (clean_and_extract, auto_link) still call the LLM,
        but suggest_update_node should return empty suggestions without calling.
        """
        state = _make_state(related_notes=[], rag_context="")

        with patch("src.services.agent_service._call_llm", return_value="{}"):
            result = create_agent_graph(llm_config).invoke(state)

        assert result.get("update_suggestions") == []

    def test_processes_when_rag_present_but_no_related(self, llm_config: MagicMock) -> None:
        """When only rag_context is present, the node should still process."""
        state = _make_state(
            related_notes=[],
            rag_context="some context",
            final_markdown="# Test Note\ncontent here " * 100,
        )

        with patch(
            "src.services.agent_service._call_llm",
            return_value='{"suggestions": []}',
        ):
            result = create_agent_graph(llm_config).invoke(state)

        assert "update_suggestions" in result

    def test_filters_none_suggestions(self, llm_config: MagicMock) -> None:
        """Suggestions with type 'none' should be filtered out."""
        state = _make_state(
            related_notes=self._related_note("Note A") + self._related_note("Note B"),
            rag_context="",
            final_markdown="# Test Note\ncontent here " * 100,
        )

        raw = json.dumps({
            "suggestions": [
                {"target_note": "Note A", "type": "none", "content": ""},
                {"target_note": "Note B", "type": "ref", "content": "See also: [[test]]"},
            ]
        })

        with patch("src.services.agent_service._call_llm", return_value=raw):
            result = create_agent_graph(llm_config).invoke(state)

        suggestions = result.get("update_suggestions", [])
        assert len(suggestions) == 1
        assert suggestions[0]["target_note"] == "Note B"
        assert suggestions[0]["type"] == "ref"

    def test_handles_json_parse_error(self, llm_config: MagicMock) -> None:
        """When LLM returns invalid JSON, the node should return empty list."""
        state = _make_state(
            related_notes=self._related_note(),
            rag_context="",
            final_markdown="# Test Note\ncontent here " * 100,
        )

        with patch("src.services.agent_service._call_llm", return_value="not valid json"):
            result = create_agent_graph(llm_config).invoke(state)

        assert result.get("update_suggestions") == []
