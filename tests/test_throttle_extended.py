"""Extended throttle module tests."""

from __future__ import annotations

from unittest.mock import patch

from src.utils.throttle import (
    PerformanceProfile,
    Profile,
    cooldown_sleep,
    get_user_idle_seconds,
    set_low_priority,
)


class TestGetUserIdleSeconds:
    def test_returns_value_or_none(self) -> None:
        result = get_user_idle_seconds()
        assert result is None or isinstance(result, int)


class TestCooldownSleep:
    def test_zero_is_instant(self) -> None:
        cooldown_sleep(0, "test")

    def test_negative_is_instant(self) -> None:
        cooldown_sleep(-1, "test")


class TestSetLowPriority:
    def test_set_low_priority_no_crash(self) -> None:
        set_low_priority("normal")


class TestPerformanceProfileExtended:
    def test_evaluate_auto_switches(self) -> None:
        profile = PerformanceProfile(mode="auto")
        with patch("src.utils.throttle.get_user_idle_seconds", return_value=600):
            result = profile.evaluate()
        assert result == Profile.IDLE

    def test_evaluate_active_when_recent_input(self) -> None:
        profile = PerformanceProfile(mode="auto")
        with patch("src.utils.throttle.get_user_idle_seconds", return_value=10):
            result = profile.evaluate()
        assert result == Profile.ACTIVE

    def test_get_params_idle(self) -> None:
        profile = PerformanceProfile(mode="idle")
        params = profile.get_params()
        assert params["parallel_workers"] >= 1
