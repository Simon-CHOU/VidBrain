"""Tests for process singleton lock."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from src.utils import singleton as singleton_mod


@pytest.fixture(autouse=True)
def isolate_pid_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    pid_file = tmp_path / "vidbrain.pid"
    monkeypatch.setattr(singleton_mod, "_PID_FILE", pid_file)
    yield pid_file
    if pid_file.is_file():
        pid_file.unlink()


class TestSingletonHelpers:
    def test_is_process_alive_invalid_pid(self) -> None:
        assert singleton_mod._is_process_alive(0) is False
        assert singleton_mod._is_process_alive(-1) is False

    def test_is_vidbrain_process_on_failure(self) -> None:
        with patch("subprocess.run", side_effect=OSError("fail")):
            assert singleton_mod._is_vidbrain_process(1234) is False


class TestAcquireSingleton:
    def test_acquire_creates_pid_file(self, isolate_pid_file: Path) -> None:
        with patch.object(singleton_mod, "_is_process_alive", return_value=False):
            singleton_mod.acquire_singleton()
        assert isolate_pid_file.read_text().strip() == str(os.getpid())
        singleton_mod._release_singleton()
        assert not isolate_pid_file.exists()

    def test_stale_pid_file_cleaned(self, isolate_pid_file: Path) -> None:
        isolate_pid_file.write_text("99999")
        with patch.object(singleton_mod, "_is_process_alive", return_value=False):
            singleton_mod.acquire_singleton()
        assert isolate_pid_file.read_text().strip() == str(os.getpid())
        singleton_mod._release_singleton()

    def test_exits_when_vidbrain_running(self, isolate_pid_file: Path) -> None:
        isolate_pid_file.write_text("42424")
        with (
            patch.object(singleton_mod, "_is_process_alive", return_value=True),
            patch.object(singleton_mod, "_is_vidbrain_process", return_value=True),
            patch.object(singleton_mod.sys, "exit", side_effect=SystemExit(1)) as mock_exit,
        ):
            with pytest.raises(SystemExit):
                singleton_mod.acquire_singleton()
            mock_exit.assert_called_once_with(1)

    def test_invalid_pid_file_content(self, isolate_pid_file: Path) -> None:
        isolate_pid_file.write_text("not-a-pid")
        with patch.object(singleton_mod, "_is_process_alive", return_value=False):
            singleton_mod.acquire_singleton()
        singleton_mod._release_singleton()
