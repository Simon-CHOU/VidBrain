"""Tests for logging utilities."""

from __future__ import annotations

import logging

from src.utils.logger import SensitiveDataFilter, _mask_sensitive, setup_logger


class TestMaskSensitive:
    def test_masks_api_key(self) -> None:
        msg = "api_key=sk-secret123"
        assert "***MASKED***" in _mask_sensitive(msg)
        assert "sk-secret" not in _mask_sensitive(msg)

    def test_masks_access_token(self) -> None:
        msg = "access_token: bearer-token"
        assert _mask_sensitive(msg) == "access_token = ***MASKED***"

    def test_plain_text_unchanged(self) -> None:
        assert _mask_sensitive("hello world") == "hello world"


class TestSensitiveDataFilter:
    def test_filter_masks_record_msg(self) -> None:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="api_key=secret",
            args=(),
            exc_info=None,
        )
        SensitiveDataFilter().filter(record)
        assert "***MASKED***" in record.msg

    def test_filter_masks_string_args(self) -> None:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="key %s",
            args=("secret_key=abc",),
            exc_info=None,
        )
        SensitiveDataFilter().filter(record)
        assert "***MASKED***" in record.args[0]


class TestSetupLogger:
    def test_setup_logger_creates_handlers(self, tmp_path) -> None:
        logger = setup_logger("vidbrain_test", log_dir=str(tmp_path / "logs"))
        assert logger.name == "vidbrain_test"
        assert len(logger.handlers) == 2
        logger.info("test message")
