"""Tests for video filename classifier."""

from __future__ import annotations

import pytest

from src.services.classifier_service import classify_video


class TestClassifyVideo:
    """Tests for classify_video function."""

    @pytest.mark.parametrize(
        "filename,expected_category",
        [
            ("Python Web开发实战.mp4", "tech"),
            ("Kubernetes生产级部署.mp4", "tech"),
            ("AI大模型应用开发.mp4", "tech"),
            ("Rust系统编程入门.mp4", "tech"),
            ("Go语言并发编程.mp4", "tech"),
            ("C++性能优化指南.mp4", "tech"),
            ("JavaScript高级程序设计.mp4", "tech"),
            ("Deep Learning 神经网络解析.mp4", "tech"),
            ("区块链Web3技术.mp4", "tech"),
            ("分布式系统设计.mp4", "tech"),
            ("机器学习实战.mp4", "tech"),
            ("Docker编排.mp4", "tech"),
            ("算法面试 LeetCode.mp4", "tech"),
            ("@TechLead_channel 最新视频.mp4", "tech"),
            ("@t3dotgg 技术分享.mp4", "tech"),
            ("编程入门教程.mp4", "tech"),
        ],
    )
    def test_tech_classification(self, filename: str, expected_category: str) -> None:
        """Should classify technical videos as 'tech'."""
        category, reason = classify_video(filename)
        assert (
            category == expected_category
        ), f"Expected {expected_category}, got {category}: {reason}"

    @pytest.mark.parametrize(
        "filename",
        [
            "抖音热门舞蹈.mp4",
            "快手搞笑日常.mp4",
            "娱乐八卦新闻.mp4",
            "日常vlog分享.mp4",
            "吃播美食探店.mp4",
            "带货直播精选.mp4",
            "美女小姐姐跳舞.mp4",
            "漫展cosplay.mp4",
            "翻唱歌曲.mp4",
            "音乐现场.mp4",
        ],
    )
    def test_skip_classification(self, filename: str) -> None:
        """Should classify non-technical videos as 'skip'."""
        category, reason = classify_video(filename)
        assert category == "skip", f"Expected skip, got {category}: {reason}"

    @pytest.mark.parametrize(
        "filename",
        [
            "未命名视频.mp4",
            "download_2025.mp4",
            "video_001.mp4",
            "output_final.mp4",
        ],
    )
    def test_unclear_classification(self, filename: str) -> None:
        """Should classify unrecognized videos as 'unclear'."""
        category, reason = classify_video(filename)
        assert category == "unclear", f"Expected unclear, got {category}: {reason}"

    def test_skip_priority_over_tech(self) -> None:
        """Should prioritize skip (blacklist) over tech (whitelist)."""
        category, _ = classify_video("抖音Python教学.mp4")  # contains both skip and tech keywords
        assert category == "skip"

    def test_case_insensitive(self) -> None:
        """Should handle case-insensitive matching."""
        category, _ = classify_video("PYTHON programming.mp4")
        assert category == "tech"

    def test_at_mention_with_lowercase(self) -> None:
        """Should match @ mentions with lowercase letters."""
        category, _ = classify_video("@tech_guru123.mp4")
        assert category == "tech"

    def test_at_mention_chinese_only(self) -> None:
        """Should not match @ followed only by Chinese characters."""
        category, _ = classify_video("@张三说.mp4")
        assert category == "unclear"

    def test_empty_filename(self) -> None:
        """Should handle empty filename."""
        category, _ = classify_video("")
        assert category == "unclear"

    def test_reason_contains_keyword(self) -> None:
        """The reason should mention the matched keyword."""
        _, reason = classify_video("Python入门教程.mp4")
        assert "Python" in reason or "python" in reason.lower()
