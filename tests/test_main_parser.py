"""Tests for CLI argument parser."""

from __future__ import annotations

import pytest

from src.main import parse_args, parse_interval


class TestParseInterval:
    """Tests for parse_interval function."""

    def test_parse_minutes(self) -> None:
        """m suffix multiplies by 60."""
        assert parse_interval("5m") == 300

    def test_parse_hours(self) -> None:
        """h suffix multiplies by 3600."""
        assert parse_interval("2h") == 7200

    def test_parse_seconds_suffix(self) -> None:
        """s suffix keeps the value."""
        assert parse_interval("90s") == 90

    def test_parse_pure_number(self) -> None:
        """Pure number is treated as seconds."""
        assert parse_interval("3600") == 3600

    def test_parse_zero(self) -> None:
        """Zero returns zero."""
        assert parse_interval("0") == 0

    def test_parse_with_spaces(self) -> None:
        """Should strip whitespace."""
        assert parse_interval("  10m  ") == 600

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("30m", 1800),
            ("2h", 7200),
            ("90s", 90),
            ("3600", 3600),
            ("0", 0),
        ],
    )
    def test_parse_interval_parametrize(self, value: str, expected: int) -> None:
        """Should parse various interval formats correctly."""
        assert parse_interval(value) == expected


class TestParseArgs:
    """Tests for parse_args function."""

    def test_default_values(self) -> None:
        """Should return default values when no arguments provided."""
        args = parse_args([])
        assert args.interval == "30m"
        assert args.batch_size == 5
        assert args.model_size == "tiny"
        assert args.once is False

    def test_custom_interval(self) -> None:
        """Should parse custom interval argument."""
        args = parse_args(["--interval", "10m"])
        assert args.interval == "10m"

    def test_once_flag(self) -> None:
        """Should set once flag."""
        args = parse_args(["--once"])
        assert args.once is True

    def test_batch_size(self) -> None:
        """Should parse batch-size as integer."""
        args = parse_args(["--batch-size", "20"])
        assert args.batch_size == 20

    def test_priority_choices(self) -> None:
        """Should accept valid priority choices."""
        args = parse_args(["--priority", "idle"])
        assert args.priority == "idle"

    def test_asr_backend_choices(self) -> None:
        """Should accept valid asr-backend choices."""
        args = parse_args(["--asr-backend", "vulkan"])
        assert args.asr_backend == "vulkan"

    def test_continuous_flag(self) -> None:
        """Should set continuous flag."""
        args = parse_args(["--continuous"])
        assert args.continuous is True
