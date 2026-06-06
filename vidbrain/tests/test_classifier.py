"""单元测试：分类器"""

from vidbrain.classifier import classify_video


class TestClassifyVideo:
    """classify_video 的单元测试——仅验证分类结果，不依赖匹配顺序"""

    # ── tech 分类 ──

    def test_tech_python(self):
        assert classify_video("Python入门教程.mp4")[0] == "tech"

    def test_tech_interview(self):
        assert classify_video("大厂面试题汇总_2024.mp4")[0] == "tech"

    def test_tech_blockchain(self):
        assert classify_video("Web3智能合约开发_Solidity入门.mp4")[0] == "tech"

    def test_tech_ai(self):
        assert classify_video("LLM大模型原理深度解析.mp4")[0] == "tech"

    def test_tech_course(self):
        assert classify_video("分布式系统实战教程_2025.mp4")[0] == "tech"

    def test_tech_lecture(self):
        assert classify_video("杨振宁科学演讲_1995.mp4")[0] == "tech"

    def test_tech_programmer(self):
        assert classify_video("程序员35岁怎么办_2024.mp4")[0] == "tech"

    def test_tech_java(self):
        assert classify_video("Java并发编程JVM调优.mp4")[0] == "tech"

    def test_tech_english_handle(self):
        assert classify_video("some_talk_2024_@TechGuru_BV1abc.mp4")[0] == "tech"

    def test_tech_redis(self):
        assert classify_video("Redis缓存一致性_2024.mp4")[0] == "tech"

    def test_tech_c_language(self):
        assert classify_video("C语言程序设计_2025.mp4")[0] == "tech"

    # ── skip 分类 ──

    def test_skip_douyin(self):
        assert classify_video("抖音_搞笑视频.mp4")[0] == "skip"

    def test_skip_vlog(self):
        assert classify_video("我的日常vlog.mp4")[0] == "skip"

    def test_skip_cosplay(self):
        assert classify_video("漫展cosplay小姐姐.mp4")[0] == "skip"

    def test_skip_entertainment(self):
        assert classify_video("搞笑短视频_娱乐.mp4")[0] == "skip"

    # ── unclear 分类 ──

    def test_unclear_random_name(self):
        assert classify_video("abc123xyz.mp4")[0] == "unclear"

    def test_unclear_uuid(self):
        assert classify_video("001747e6-0b68-11f0-8cd4.mp4")[0] == "unclear"

    def test_unclear_bv_only(self):
        """BV 编号不再独立作为 tech 判定依据"""
        assert classify_video("BV1KrJAzDEk5.mp4")[0] == "unclear"

    # ── 边界情况 ──

    def test_bv_with_keyword_is_tech(self):
        """BV + 技术关键词 = tech"""
        assert classify_video("Python教程_BV1abc123.mp4")[0] == "tech"
