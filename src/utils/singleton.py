"""
进程级单实例锁。

通过 PID 文件机制确保同一时刻只有一个 VidBrain 实例运行，
无论是通过 run_daemon.ps1 启动还是直接 python -m vidbrain.main 启动。
"""

from __future__ import annotations

import atexit
import ctypes
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("vidbrain.singleton")

# ── PID 文件路径 ──
_PID_FILE = Path("logs") / "vidbrain.pid"

# ── Windows API 常量 ──
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259  # STILL_ACTIVE


def _is_process_alive(pid: int) -> bool:
    """检查指定 PID 的进程是否仍在运行 (Windows)。"""
    if pid <= 0:
        return False
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        exit_code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        kernel32.CloseHandle(handle)
        return exit_code.value == _STILL_ACTIVE
    except OSError:
        return False


def _is_vidbrain_process(pid: int) -> bool:
    """检查指定 PID 的进程是否为 VidBrain 进程 (Windows)。

    通过进程命令行中是否包含 'vidbrain' 来判断。
    """
    try:
        import subprocess

        result = subprocess.run(
            ["wmic", "process", "where", f"ProcessId={pid}", "get", "CommandLine"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        output = result.stdout.lower()
        return "vidbrain" in output
    except Exception:
        # 无法确认时不阻塞启动，保守地认为不是 VidBrain 进程
        return False


def acquire_singleton() -> None:
    """获取单实例锁。

    如果已有另一个 VidBrain 实例在运行，打印告警并以 exit code 1 退出。
    如果 PID 文件残留（原进程已死或 PID 被复用），清理后继续。

    注册 atexit 回调，在正常退出时自动释放锁。
    """
    pid_file = _PID_FILE

    # 确保 logs 目录存在
    pid_file.parent.mkdir(parents=True, exist_ok=True)

    # ── 检查是否存在 PID 文件 ──
    if pid_file.is_file():
        try:
            stale_pid = int(pid_file.read_text().strip())
        except (ValueError, OSError):
            stale_pid = -1

        if _is_process_alive(stale_pid):
            # 进程存活，检查是否为 VidBrain
            if _is_vidbrain_process(stale_pid):
                logger.error(
                    "VidBrain 已有实例在运行 (PID: %d)。"
                    " 同一时刻只能运行一个 VidBrain 实例。"
                    " 如需重启，请先停止现有实例。",
                    stale_pid,
                )
                print(
                    f"[VidBrain] 错误: 已有实例在运行 (PID: {stale_pid})。"
                    f" 同一时刻只能运行一个 VidBrain 实例。",
                    file=sys.stderr,
                )
                sys.exit(1)
            else:
                # PID 被非 VidBrain 进程复用
                logger.warning(
                    "PID 文件中的 PID %d 被非 VidBrain 进程复用，清理残留文件",
                    stale_pid,
                )
        else:
            # 原进程已死，清理残留
            logger.info(
                "检测到残留 PID 文件 (PID %d 已不存在)，清理后继续启动",
                stale_pid,
            )

        # 删除残留/无效的 PID 文件
        try:
            pid_file.unlink()
        except OSError:
            pass

    # ── 写入当前进程 PID ──
    current_pid = os.getpid()
    pid_file.write_text(str(current_pid))
    logger.info("单实例锁已获取 (PID: %d)", current_pid)

    # ── 注册退出清理 ──
    atexit.register(_release_singleton)
    # 也注册信号处理（SIGINT/SIGTERM 退出时 atexit 会触发）


def _release_singleton() -> None:
    """释放单实例锁，删除 PID 文件。"""
    pid_file = _PID_FILE
    try:
        if pid_file.is_file():
            pid_file.unlink()
            logger.info("单实例锁已释放")
    except OSError:
        pass
