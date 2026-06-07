"""Tests for dynamic performance profile module."""

import sys
import time
from unittest.mock import patch

import pytest

from vidbrain.throttle import (
    PerformanceProfile,
    Profile,
    get_user_idle_seconds,
    set_low_priority,
    USER_IDLE_THRESHOLD_SECONDS,
)


class TestGetUserIdeSeconds:
    def test_returns_int_or_none(self):
        """idle detection returns int or None (no crash)."""
        result = get_user_idle_seconds()
        assert result is None or isinstance(result, int)


class TestPerformanceProfile:
    def test_fixed_idle_no_switch(self):
        """Fixed idle mode never switches."""
        p = PerformanceProfile(mode="idle")
        assert p.current == Profile.IDLE
        assert p.mode == "idle"

        # Try to evaluate - should stay idle regardless
        for _ in range(5):
            p.evaluate()
            assert p.current == Profile.IDLE

    def test_fixed_active_no_switch(self):
        """Fixed active mode never switches."""
        p = PerformanceProfile(mode="active")
        assert p.current == Profile.ACTIVE
        assert p.mode == "active"

        for _ in range(5):
            p.evaluate()
            assert p.current == Profile.ACTIVE

    def test_auto_starts_idle(self):
        """Auto mode starts in idle state."""
        p = PerformanceProfile(mode="auto")
        assert p.current == Profile.IDLE

    @pytest.mark.parametrize("mode", ["idle", "active"])
    def test_get_params_returns_expected_keys(self, mode):
        """get_params returns all expected keys."""
        p = PerformanceProfile(mode=mode)
        params = p.get_params()
        for key in ("priority", "parallel_workers", "cpu_threads_per_worker",
                     "video_cooldown_seconds"):
            assert key in params

    def test_get_params_for_specific_profile(self):
        """get_params can query a specific profile."""
        p = PerformanceProfile(mode="idle")
        idle_params = p.get_params(Profile.IDLE)
        active_params = p.get_params(Profile.ACTIVE)
        assert idle_params["parallel_workers"] == 2
        assert active_params["parallel_workers"] == 1
        assert idle_params["cpu_threads_per_worker"] > active_params["cpu_threads_per_worker"]

    def test_is_idle_active(self):
        """is_idle_active reflects current state."""
        p = PerformanceProfile(mode="idle")
        assert p.is_idle_active() is True

        p2 = PerformanceProfile(mode="active")
        assert p2.is_idle_active() is False

    def test_evaluate_idle_to_active(self):
        """When user is active (< 5 min), switch to ACTIVE immediately."""
        p = PerformanceProfile(mode="auto")
        # Force to IDLE first
        p._current = Profile.IDLE
        p._consecutive_idle_checks = 0

        with patch("vidbrain.throttle.get_user_idle_seconds", return_value=60):
            # User active for 60s → should switch to ACTIVE
            result = p.evaluate(now=time.time())
            assert result == Profile.ACTIVE
            assert p._consecutive_idle_checks == 0  # reset

    def test_evaluate_active_to_idle_requires_debounce(self):
        """Active → Idle requires 2 consecutive idle confirmations."""
        p = PerformanceProfile(mode="auto")
        p._current = Profile.ACTIVE
        p._consecutive_idle_checks = 0

        with patch("vidbrain.throttle.get_user_idle_seconds", return_value=400):
            # First idle detection → not enough yet
            result = p.evaluate(now=time.time())
            assert result == Profile.ACTIVE  # still active
            assert p._consecutive_idle_checks == 1

            # Second idle detection → should switch
            result = p.evaluate(now=time.time() + 61)  # > detection interval
            assert result == Profile.IDLE
            assert p._consecutive_idle_checks == 2

    def test_evaluate_respects_detection_interval(self):
        """evaluate doesn't re-detect within the interval window."""
        p = PerformanceProfile(mode="auto")
        p._current = Profile.ACTIVE
        p._consecutive_idle_checks = 0

        now = time.time()
        with patch("vidbrain.throttle.get_user_idle_seconds", return_value=400):
            # First call: counts as idle check 1
            p.evaluate(now=now)
            assert p._consecutive_idle_checks == 1

            # Second call immediately after: should NOT re-evaluate
            p.evaluate(now=now + 5)
            assert p._consecutive_idle_checks == 1  # still 1 (cooldown)

            # Third call after interval: should increment
            p.evaluate(now=now + 61)
            assert p._consecutive_idle_checks == 2

    def test_non_windows_fallback(self):
        """Non-Windows platforms fall back to fixed idle."""
        p = PerformanceProfile(mode="auto")
        p._current = Profile.ACTIVE

        with patch("vidbrain.throttle.get_user_idle_seconds", return_value=None):
            result = p.evaluate(now=time.time())
            assert result == Profile.IDLE  # fallback to idle

    def test_apply_calls_set_low_priority(self):
        """apply() invokes set_low_priority with correct level."""
        p = PerformanceProfile(mode="idle")
        with patch("vidbrain.throttle.set_low_priority") as mock_set:
            p.apply(Profile.IDLE)
            mock_set.assert_called_with("normal")

        with patch("vidbrain.throttle.set_low_priority") as mock_set:
            p.apply(Profile.ACTIVE)
            mock_set.assert_called_with("below_normal")


class TestSetLowPriority:
    def test_set_normal_is_noop(self):
        """Setting normal priority always returns True."""
        assert set_low_priority("normal") is True

    def test_set_invalid_level(self):
        """Invalid level returns False."""
        assert set_low_priority("ultra_high") is False
