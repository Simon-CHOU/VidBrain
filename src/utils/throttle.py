"""
资源调控模块：进程优先级 + 线程优先级 + 冷却间隔 + 动态性能 Profile。

仅在 Windows 上生效；非 Windows 平台会静默降级。

动态 Profile 通过检测桌面空闲状态自动在"满负荷"和"省电"模式间切换。
"""

from __future__ import annotations

import ctypes
import enum
import logging
import multiprocessing
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

# ── 桌面空闲检测 ──
USER_IDLE_THRESHOLD_SECONDS = 300  # 5 分钟
_IDLE_DETECTION_INTERVAL = 60  # 检测间隔（秒）


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("dwTime", ctypes.c_uint),
    ]


def get_user_idle_seconds() -> int | None:
    """获取用户距离最后一次键盘/鼠标输入的秒数。

    通过 Windows GetLastInputInfo API 检测。
    非 Windows 平台返回 None（不可用）。

    Returns:
        空闲秒数，或 None 表示不可用。
    """
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return None

    lii = _LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(_LASTINPUTINFO)

    if not user32.GetLastInputInfo(ctypes.byref(lii)):
        return None

    tick_count = kernel32.GetTickCount()
    return int((tick_count - lii.dwTime) / 1000)


# ── 动态性能 Profile ──


class Profile(enum.Enum):
    """性能 Profile 枚举。"""

    IDLE = "idle"  # 无人值守：满负荷
    ACTIVE = "active"  # 桌面活跃：省电降速


# Profile 参数映射
_PROFILE_PARAMS: dict[Profile, dict] = {
    Profile.IDLE: {
        "priority": "normal",
        "parallel_workers": 2,
        "cpu_threads_per_worker": max(2, multiprocessing.cpu_count() - 2),
        "video_cooldown_seconds": 0,
        "label": "满负荷模式",
    },
    Profile.ACTIVE: {
        "priority": "below_normal",
        "parallel_workers": 1,
        "cpu_threads_per_worker": 2,
        "video_cooldown_seconds": 10,
        "label": "省电模式",
    },
}


class PerformanceProfile:
    """动态性能管理状态机。

    检测桌面空闲状态，自动在 idle（满负荷）和 active（省电）之间切换。
    支持固定模式（--profile idle|active）和自动模式（--profile auto）。
    """

    def __init__(self, mode: str = "auto") -> None:
        """
        Args:
            mode: "auto"（自动切换）, "idle"（固定满负荷）, "active"（固定省电）
        """
        self._mode = mode  # "auto" | "idle" | "active"
        self._current: Profile = Profile.IDLE
        self._consecutive_idle_checks: int = 0
        self._last_evaluation_time: float = 0.0

        # 固定模式直接设置
        if mode == "active":
            self._current = Profile.ACTIVE
        elif mode == "idle":
            self._current = Profile.IDLE

        self.apply(self._current)
        logger.info(
            "性能 Profile: %s — %s", self._current.value, _PROFILE_PARAMS[self._current]["label"]
        )

    @property
    def current(self) -> Profile:
        return self._current

    @property
    def mode(self) -> str:
        return self._mode

    def evaluate(self, now: float | None = None) -> Profile:
        """评估当前应该使用的 profile。

        仅在 auto 模式下检测桌面空闲状态并切换。
        固定模式始终返回固定 profile。

        Args:
            now: 当前时间戳（用于去抖），默认 time.time()

        Returns:
            应使用的 Profile 枚举值。
        """
        if now is None:
            now = time.time()

        # 固定模式不切换
        if self._mode != "auto":
            return self._current

        # 限制检测频率
        if now - self._last_evaluation_time < _IDLE_DETECTION_INTERVAL:
            return self._current
        self._last_evaluation_time = now

        idle_seconds = get_user_idle_seconds()
        if idle_seconds is None:
            # 非 Windows：固定 idle
            if self._current != Profile.IDLE:
                self._switch_to(Profile.IDLE, "桌面空闲检测不可用，固定为 idle profile")
            return self._current

        if idle_seconds < USER_IDLE_THRESHOLD_SECONDS:
            # 桌面活跃 → active
            self._consecutive_idle_checks = 0
            if self._current != Profile.ACTIVE:
                self._switch_to(
                    Profile.ACTIVE,
                    f"检测到桌面活跃 (空闲 {idle_seconds}s < {USER_IDLE_THRESHOLD_SECONDS}s)",
                )
        else:
            # 桌面闲置 → 需要连续确认
            self._consecutive_idle_checks += 1
            if self._consecutive_idle_checks >= 2 and self._current != Profile.IDLE:
                self._switch_to(
                    Profile.IDLE,
                    f"桌面闲置 ≥ {USER_IDLE_THRESHOLD_SECONDS}s "
                    f"(确认 {self._consecutive_idle_checks} 次)",
                )

        return self._current

    def apply(self, profile: Profile) -> None:
        """应用 profile 的进程优先级设置。"""
        params = _PROFILE_PARAMS[profile]
        set_low_priority(params["priority"])

    def get_params(self, profile: Profile | None = None) -> dict:
        """获取 profile 的运行参数。

        Args:
            profile: 目标 profile，默认当前 profile

        Returns:
            包含 priority, parallel_workers, cpu_threads_per_worker,
            video_cooldown_seconds 的字典。
        """
        return dict(_PROFILE_PARAMS[profile or self._current])

    def _switch_to(self, new_profile: Profile, reason: str) -> None:
        """执行 profile 切换。"""
        old = self._current
        self._current = new_profile
        self.apply(new_profile)
        logger.info("性能 Profile 切换: %s → %s (%s)", old.value, new_profile.value, reason)

    def is_idle_active(self) -> bool:
        """当前是否处于 idle（满负荷）状态。"""
        return self._current == Profile.IDLE


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
        handle = ctypes.windll.kernel32.GetCurrentProcess()  # type: ignore[attr-defined]
        result = ctypes.windll.kernel32.SetPriorityClass(handle, requested_class)  # type: ignore[attr-defined]
        if result:
            logger.info("进程优先级已设置为: %s", level)
            return True
        else:
            logger.debug("SetPriorityClass 调用失败 (level=%s) — 非 Windows 或无权限环境下属于正常现象", level)
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
        handle = ctypes.windll.kernel32.GetCurrentThread()  # type: ignore[attr-defined]
        result = ctypes.windll.kernel32.SetThreadPriority(handle, THREAD_PRIORITY_LOWEST)  # type: ignore[attr-defined]
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
