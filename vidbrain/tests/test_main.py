"""单元测试：主入口工具函数"""

from vidbrain.main import parse_interval


class TestParseInterval:
    def test_seconds(self):
        assert parse_interval("30s") == 30

    def test_minutes(self):
        assert parse_interval("30m") == 1800

    def test_hours(self):
        assert parse_interval("2h") == 7200

    def test_raw_seconds(self):
        assert parse_interval("3600") == 3600

    def test_zero(self):
        assert parse_interval("0") == 0
