"""
资源调控模块：进程优先级 + 线程优先级 + 冷却间隔。

仅在 Windows 上生效；非 Windows 平台会静默降级。
"""

from __future__ import annotations

import ctypes
import logging
import time

logger = logging.getLogger("vidbrain.throttle")

# ── Windows 进程优先级常量 ──
_NORMAL = 0x00000020
_BELOW_NORMAL = 0x00004000
_IDLE = 0x00000040

# ── Windows 线程优先级常量 ──
THREAD_PRIORITY_LOWEST = -2

# ── 级别映射 ──
_LEVEL_MAP: dict[str, int] = {
    "below_normal": _BELOW_NORMAL,
    "idle": _IDLE,
    "normal": _NORMAL,
}


def set_low_priority(level: str = "below_normal") -> bool:
    """设置当前进程优先级。

    Args:
        level: 优先级级别，可选 "normal" / "below_normal" / "idle"。
               "normal" 为无操作（直接返回 True）。

    Returns:
        True 表示设置成功；False 表示平台不支持或执行失败。
    """
    if level not in _LEVEL_MAP:
        logger.warning("无效的优先级级别: %s", level)
        return False

    requested_class = _LEVEL_MAP[level]

    # "normal" 无需任何操作
    if requested_class == _NORMAL:
        logger.info("进程优先级: 保持 normal（默认）")
        return True

    try:
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        result = ctypes.windll.kernel32.SetPriorityClass(handle, requested_class)
        if result:
            logger.info("进程优先级已设置为: %s", level)
            return True
        else:
            logger.warning("SetPriorityClass 调用失败 (level=%s)", level)
            return False
    except Exception:
        logger.warning("无法设置进程优先级（可能非 Windows 平台），已跳过")
        return False


def apply_idle_priority() -> bool:
    """设置进程为 idle 优先级 + 当前线程为最低优先级。

    Returns:
        True 表示至少进程优先级设置成功；False 表示均失败。
    """
    process_ok = set_low_priority("idle")

    try:
        handle = ctypes.windll.kernel32.GetCurrentThread()
        result = ctypes.windll.kernel32.SetThreadPriority(handle, THREAD_PRIORITY_LOWEST)
        if result:
            logger.info("线程优先级已设置为 THREAD_PRIORITY_LOWEST")
        else:
            logger.debug("SetThreadPriority 调用失败（已忽略，不影响主流程）")
    except Exception:
        logger.debug("无法设置线程优先级（已忽略）")

    return process_ok


def cooldown_sleep(seconds: int, reason: str = "") -> None:
    """冷却睡眠。

    Args:
        seconds: 睡眠秒数（<= 0 时直接返回）。
        reason: 冷却原因说明。
    """
    if seconds <= 0:
        return

    logger.info("冷却 %ds: %s", seconds, reason)
    time.sleep(seconds)
