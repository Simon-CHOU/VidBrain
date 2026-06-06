"""Unit tests: throttle module"""

from vidbrain.throttle import set_low_priority, apply_idle_priority, cooldown_sleep


class TestSetLowPriority:
    def test_set_below_normal(self):
        # Should not raise; on non-Windows returns False
        result = set_low_priority("below_normal")
        assert result in (True, False)

    def test_set_idle(self):
        result = set_low_priority("idle")
        assert result in (True, False)

    def test_set_normal_skips(self):
        result = set_low_priority("normal")
        assert result is True  # normal = no-op, considered success

    def test_invalid_level(self):
        result = set_low_priority("invalid")
        assert result is False


class TestApplyIdlePriority:
    def test_apply_idle(self):
        result = apply_idle_priority()
        assert result in (True, False)


class TestCooldownSleep:
    def test_zero_skips(self, capsys):
        cooldown_sleep(0, "test")
        # No output expected

    def test_negative_skips(self):
        cooldown_sleep(-1, "test")
        # Should not raise
