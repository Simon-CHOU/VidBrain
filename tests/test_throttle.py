"""Tests for resource throttle module."""

from __future__ import annotations

from src.utils.throttle import cooldown_sleep


class TestCooldownSleep:
    """Tests for cooldown_sleep function."""

    def test_zero_seconds_returns_immediately(self) -> None:
        """Should return immediately when seconds is 0."""
        cooldown_sleep(0, "test")

    def test_negative_seconds_returns_immediately(self) -> None:
        """Should return immediately when seconds is negative."""
        cooldown_sleep(-1, "test")


class TestPerformanceProfile:
    """Tests for PerformanceProfile class."""

    def test_fixed_idle_mode(self) -> None:
        """Fixed idle mode should stay idle."""
        from src.utils.throttle import PerformanceProfile, Profile

        pp = PerformanceProfile(mode="idle")
        assert pp.current == Profile.IDLE
        result = pp.evaluate()
        assert result == Profile.IDLE

    def test_fixed_active_mode(self) -> None:
        """Fixed active mode should stay active."""
        from src.utils.throttle import PerformanceProfile, Profile

        pp = PerformanceProfile(mode="active")
        assert pp.current == Profile.ACTIVE

    def test_get_params_returns_dict(self) -> None:
        """get_params should return a dict with expected keys."""
        from src.utils.throttle import PerformanceProfile

        pp = PerformanceProfile(mode="idle")
        params = pp.get_params()
        assert "priority" in params
        assert "parallel_workers" in params
        assert "cpu_threads_per_worker" in params
        assert "video_cooldown_seconds" in params

    def test_is_idle_active(self) -> None:
        """is_idle_active should reflect current state."""
        from src.utils.throttle import PerformanceProfile

        pp = PerformanceProfile(mode="idle")
        assert pp.is_idle_active() is True
        pp2 = PerformanceProfile(mode="active")
        assert pp2.is_idle_active() is False
