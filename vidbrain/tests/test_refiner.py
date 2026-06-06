"""单元测试：知识库精炼器"""

from vidbrain.refiner import parse_links, analyze_links


class TestParseLinks:
    def test_single_link(self):
        text = "这是一个 [[双链]] 测试"
        assert parse_links(text) == ["双链"]

    def test_multiple_links(self):
        text = "[[概念A]] 和 [[概念B]] 是相关的"
        assert parse_links(text) == ["概念A", "概念B"]

    def test_link_with_alias(self):
        text = "参考 [[概念|显示名]] 了解更多"
        assert parse_links(text) == ["概念"]

    def test_no_links(self):
        text = "这是一段普通文本"
        assert parse_links(text) == []

    def test_empty_string(self):
        assert parse_links("") == []


class TestAnalyzeLinks:
    def test_basic_analysis(self):
        notes = [
            {"name": "A", "outgoing_links": ["B", "C"], "content": "", "path": ""},
            {"name": "B", "outgoing_links": ["C"], "content": "", "path": ""},
            {"name": "C", "outgoing_links": [], "content": "", "path": ""},
        ]
        report = analyze_links(notes)
        assert report["outgoing_counts"] == {"A": 2, "B": 1, "C": 0}
        assert report["incoming_counts"] == {"B": 1, "C": 2}
        assert len(report["orphan_no_outgoing"]) == 1  # C
        assert report["orphan_no_outgoing"][0]["name"] == "C"

    def test_all_orphans(self):
        notes = [
            {"name": "A", "outgoing_links": [], "content": "", "path": ""},
            {"name": "B", "outgoing_links": [], "content": "", "path": ""},
        ]
        report = analyze_links(notes)
        assert len(report["orphan_no_outgoing"]) == 2
        assert len(report["orphan_no_incoming"]) == 2
