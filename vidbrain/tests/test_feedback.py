"""单元测试：用户反馈闭环模块"""

import tempfile
import time
from datetime import datetime
from pathlib import Path

from vidbrain.feedback import (
    parse_front_matter,
    detect_user_edits,
    extract_feedback_signals,
    get_feedback_context,
)


class TestParseFrontMatter:
    def test_parse_front_matter_basic(self):
        content = (
            "---\n"
            "type: technical-note\n"
            "status: auto-generated\n"
            "source_video: some-video.mp4\n"
            "created: 2026-06-01 12:00:00\n"
            "---\n\n"
            "## Some Content\n"
            "Hello World\n"
        )
        fm = parse_front_matter(content)
        assert fm["type"] == "technical-note"
        assert fm["status"] == "auto-generated"
        assert fm["source_video"] == "some-video.mp4"
        assert fm["created"] == "2026-06-01 12:00:00"

    def test_parse_front_matter_with_bool(self):
        content = (
            "---\n"
            "reviewed: true\n"
            "quality: high\n"
            "---\n\n"
            "Content\n"
        )
        fm = parse_front_matter(content)
        assert fm["reviewed"] is True
        assert fm["quality"] == "high"

    def test_parse_front_matter_none(self):
        content = "## No Front Matter\n\nJust some markdown content.\n"
        fm = parse_front_matter(content)
        assert fm == {}

    def test_parse_front_matter_empty(self):
        fm = parse_front_matter("")
        assert fm == {}

    def test_parse_front_matter_only_open_delim(self):
        content = "---\nSome stuff but no closing delim"
        fm = parse_front_matter(content)
        assert fm == {}


class TestDetectUserEdits:
    def test_detect_user_edits_empty_vault(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = detect_user_edits(tmp)
            assert result == []

    def test_detect_user_edits_nonexistent_vault(self):
        result = detect_user_edits(r"Z:\nonexistent\path\to\vault")
        assert result == []

    def test_detect_user_edits_skips_drafts(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            drafts_dir = vault / "_drafts"
            drafts_dir.mkdir()
            (drafts_dir / "draft_note.md").write_text(
                "---\nstatus: draft\ncreated: 2026-06-01 12:00:00\n---\n\n# Draft\n",
                encoding="utf-8",
            )
            result = detect_user_edits(tmp)
            assert len(result) == 0

    def test_detect_user_edits_skips_moc(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "MOC-AI Infrastructure.md").write_text(
                "---\nstatus: moc\ncreated: 2026-06-01 12:00:00\n---\n\n# MOC\n",
                encoding="utf-8",
            )
            result = detect_user_edits(tmp)
            assert len(result) == 0

    def test_detect_user_edits_with_temp_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            now = datetime.now()

            # Note 1: recently created, should NOT be marked as user_edited
            note1 = vault / "note_fresh.md"
            note1.write_text(
                "---\n"
                "status: auto-generated\n"
                f"created: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
                "---\n\n"
                "## Note 1 Content\n",
                encoding="utf-8",
            )

            # Note 2: created long ago, should be marked as user_edited
            old_time = now.replace(year=now.year - 1)
            note2 = vault / "note_edited.md"
            note2.write_text(
                "---\n"
                "status: auto-generated\n"
                f"created: {old_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                "---\n\n"
                "## Note 2 Content (edited by user)\n",
                encoding="utf-8",
            )

            # Note 3: reviewed by user
            note3 = vault / "note_reviewed.md"
            note3.write_text(
                "---\n"
                "status: auto-generated\n"
                f"created: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
                "reviewed: true\n"
                "reviewed_at: 2026-06-02 10:00:00\n"
                "---\n\n"
                "## Note 3 Content\n",
                encoding="utf-8",
            )

            result = detect_user_edits(tmp)

            assert len(result) == 3

            fresh = [r for r in result if r["name"] == "note_fresh"][0]
            assert fresh["user_edited"] is False
            assert fresh["reviewed"] is False

            edited = [r for r in result if r["name"] == "note_edited"][0]
            assert edited["user_edited"] is True
            assert edited["reviewed"] is False
            assert edited["status"] == "auto-generated"

            reviewed = [r for r in result if r["name"] == "note_reviewed"][0]
            assert reviewed["reviewed"] is True

    def test_detect_user_edits_no_frontmatter(self):
        """Notes without front-matter and without status should be skipped."""
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "manual_note.md").write_text(
                "# Manual Note\n\nSome content\n", encoding="utf-8"
            )
            result = detect_user_edits(tmp)
            assert len(result) == 0


class TestExtractFeedbackSignals:
    def test_extract_feedback_signals_empty(self):
        result = extract_feedback_signals("/fake/path", [])
        assert result["preferred_links"] == []
        assert result["avoid_links"] == []
        assert result["edited_count"] == 0
        assert result["reviewed_count"] == 0

    def test_extract_feedback_signals_no_edited(self):
        edited_notes = [
            {"name": "a", "path": "/tmp/a.md", "status": "auto-generated",
             "user_edited": False, "reviewed": True},
        ]
        result = extract_feedback_signals("/fake/path", edited_notes)
        assert result["preferred_links"] == []
        assert result["edited_count"] == 0
        assert result["reviewed_count"] == 1

    def test_extract_preferred_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            # Note A: has links CUDA, GPU
            (vault / "a.md").write_text(
                "---\nstatus: auto-generated\ncreated: 2020-01-01 00:00:00\n---\n\n"
                "[[CUDA]] and [[GPU]] topics\n",
                encoding="utf-8",
            )
            # Note B: has links CUDA, TensorCore
            (vault / "b.md").write_text(
                "---\nstatus: auto-generated\ncreated: 2020-01-01 00:00:00\n---\n\n"
                "[[CUDA]] and [[TensorCore]]\n",
                encoding="utf-8",
            )

            edited_notes = [
                {"name": "a", "path": str(vault / "a.md"), "status": "auto-generated",
                 "user_edited": True, "reviewed": False},
                {"name": "b", "path": str(vault / "b.md"), "status": "auto-generated",
                 "user_edited": True, "reviewed": False},
            ]

            result = extract_feedback_signals(tmp, edited_notes)
            assert result["edited_count"] == 2
            # CUDA appears in both → preferred; GPU and TensorCore appear only once
            assert "CUDA" in result["preferred_links"]
            assert "GPU" not in result["preferred_links"]
            assert "TensorCore" not in result["preferred_links"]


class TestGetFeedbackContext:
    def test_get_feedback_context_empty(self):
        signals = {
            "preferred_links": [],
            "avoid_links": [],
            "edited_count": 0,
            "reviewed_count": 0,
        }
        result = get_feedback_context(signals)
        assert result == ""

    def test_get_feedback_context_with_preferred_links(self):
        signals = {
            "preferred_links": ["CUDA", "GPU"],
            "avoid_links": [],
            "edited_count": 3,
            "reviewed_count": 1,
        }
        result = get_feedback_context(signals)
        assert "用户偏好链接" in result
        assert "[[CUDA]]" in result
        assert "[[GPU]]" in result
        assert "3 篇笔记曾被用户编辑" in result
        assert "1 篇笔记已被用户审核" in result

    def test_get_feedback_context_edited_only(self):
        signals = {
            "preferred_links": [],
            "avoid_links": [],
            "edited_count": 5,
            "reviewed_count": 0,
        }
        result = get_feedback_context(signals)
        assert "5 篇笔记曾被用户编辑" in result
        assert "用户偏好链接" not in result
        assert "审核" not in result
