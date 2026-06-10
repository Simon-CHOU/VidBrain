"""
远端 ASR 客户端与远端优先路由封装。

当前实现包含：
- 显式 endpoint 的 worker HTTP 调用
- 主控侧远端优先
- 远端失败时任务级回退到本地 CPU
- 远端健康检查、熔断、冷却与自动恢复探测
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger("vidbrain.remote_asr")


class RemoteASRError(RuntimeError):
    """远端 ASR 调用失败。"""


class RemoteCircuitState(str, Enum):
    """远端路由状态机。"""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class RemoteASRClient:
    """worker `/healthz` 与 `/inference` 的轻量客户端。"""

    def __init__(self, host: str, port: int, timeout_seconds: float = 2.0) -> None:
        self._host = host.strip()
        self._port = port
        self._timeout_seconds = timeout_seconds
        self._base_url = f"http://{self._host}:{self._port}"

    @property
    def endpoint(self) -> str:
        """返回远端 endpoint。"""
        return self._base_url

    def check_health(self) -> dict[str, Any]:
        """探测 worker 健康状态。"""
        payload = self._request_json(
            Request(
                url=f"{self._base_url}/healthz",
                method="GET",
            )
        )
        if payload.get("status") != "ok":
            raise RemoteASRError(f"远端健康检查失败: {payload}")
        return payload

    def transcribe(self, file_path: str) -> list[dict[str, Any]]:
        """上传文件内容到 worker 并返回统一的 ASR 段列表。"""
        file_bytes = Path(file_path).read_bytes()
        payload = self._request_json(
            Request(
                url=f"{self._base_url}/inference",
                data=file_bytes,
                headers={"Content-Type": "application/octet-stream"},
                method="POST",
            )
        )
        if payload.get("status") != "ok":
            raise RemoteASRError(f"远端 ASR 返回错误: {payload}")

        segments = payload.get("segments")
        if not isinstance(segments, list):
            raise RemoteASRError("远端 ASR 响应缺少 segments 列表")
        return segments

    def _request_json(self, request: Request) -> dict[str, Any]:
        """发送 HTTP 请求并解析 JSON 响应。"""
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = self._read_error_body(exc)
            raise RemoteASRError(
                f"远端请求失败: status={exc.code}, endpoint={request.full_url}, detail={detail}"
            ) from exc
        except URLError as exc:
            reason = exc.reason
            if isinstance(reason, socket.timeout):
                raise RemoteASRError(
                    f"远端请求超时: endpoint={request.full_url}, timeout={self._timeout_seconds}s"
                ) from exc
            raise RemoteASRError(
                f"远端连接失败: endpoint={request.full_url}, reason={reason}"
            ) from exc
        except TimeoutError as exc:
            raise RemoteASRError(
                f"远端请求超时: endpoint={request.full_url}, timeout={self._timeout_seconds}s"
            ) from exc

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RemoteASRError(f"远端返回了无效 JSON: {body[:200]}") from exc

        if not isinstance(payload, dict):
            raise RemoteASRError("远端响应必须是 JSON 对象")
        return payload

    @staticmethod
    def _read_error_body(exc: HTTPError) -> str:
        """尽量读取 HTTP 错误响应内容。"""
        try:
            body = exc.read().decode("utf-8")
        except Exception:
            return ""
        return body[:200]


class RemoteFirstASREngine:
    """远端优先、本地 CPU 回退的组合 ASR 引擎。"""

    def __init__(
        self,
        remote_client: RemoteASRClient,
        local_cpu_engine: Any,
        health_interval_seconds: int = 10,
        failure_threshold: int = 2,
        recovery_threshold: int = 2,
        cooldown_seconds: int = 60,
        time_fn: Any | None = None,
    ) -> None:
        self._remote_client = remote_client
        self._local_cpu_engine = local_cpu_engine
        self._health_interval_seconds = max(0, health_interval_seconds)
        self._failure_threshold = max(1, failure_threshold)
        self._recovery_threshold = max(1, recovery_threshold)
        self._cooldown_seconds = max(0, cooldown_seconds)
        self._time_fn = time_fn or time.time
        self._lock = threading.Lock()
        self._state = RemoteCircuitState.OPEN
        self._consecutive_failures = 0
        self._consecutive_recovery_successes = 0
        self._cooldown_until = 0.0
        self._next_health_check_after = 0.0
        self._next_recovery_probe_after = 0.0

    @property
    def remote_ready(self) -> bool:
        """返回当前是否恢复到远端优先。"""
        with self._lock:
            return self._state == RemoteCircuitState.CLOSED

    @property
    def state(self) -> str:
        """返回当前状态机状态。"""
        with self._lock:
            return self._state.value

    def bootstrap(self) -> None:
        """启动时执行一次 worker 健康探测。"""
        try:
            health = self._remote_client.check_health()
        except RemoteASRError:
            self._open_circuit(reason="bootstrap")
            raise

        self._close_circuit()
        logger.info(
            "远端 ASR worker 已就绪: endpoint=%s, backend=%s, model=%s",
            self._remote_client.endpoint,
            health.get("backend", "unknown"),
            health.get("model_size", "unknown"),
        )

    def transcribe(self, file_path: str) -> list[dict[str, Any]]:
        """优先使用远端；失败或未就绪时回退到本地 CPU。"""
        should_fallback_current_task = self._run_health_checks_if_needed()
        if self.remote_ready and not should_fallback_current_task:
            try:
                logger.info("优先使用远端 ASR: %s", self._remote_client.endpoint)
                segments = self._remote_client.transcribe(file_path)
                self._record_remote_success()
                return segments
            except RemoteASRError as exc:
                self._record_remote_failure(exc)
                logger.warning("远端 ASR 失败，回退本地 CPU: %s", exc)

        logger.info("使用本地 CPU ASR 回退: %s", file_path)
        return self._local_cpu_engine.transcribe(file_path)

    def _run_health_checks_if_needed(self) -> bool:
        """在任务前按状态机执行健康检查或恢复探测。

        返回值为当前任务是否应直接回退本地 CPU。
        """
        now = self._time_fn()

        with self._lock:
            if self._state == RemoteCircuitState.CLOSED:
                if now < self._next_health_check_after:
                    return False
                self._next_health_check_after = now + self._health_interval_seconds
                probe_kind = "health"
            else:
                if now < self._cooldown_until or now < self._next_recovery_probe_after:
                    return False
                self._next_recovery_probe_after = now + self._health_interval_seconds
                probe_kind = "recovery"

        try:
            health = self._remote_client.check_health()
        except RemoteASRError as exc:
            if probe_kind == "health":
                self._record_remote_failure(exc)
                logger.warning("远端健康检查失败，当前任务回退本地 CPU: %s", exc)
            else:
                self._handle_recovery_probe_failure(exc)
            return True

        if probe_kind == "health":
            self._record_health_success()
            logger.debug("远端健康检查成功: %s", self._remote_client.endpoint)
            return False
        else:
            self._handle_recovery_probe_success(health)
            return True

    def _record_health_success(self) -> None:
        """关闭态健康检查成功后清理失败计数。"""
        with self._lock:
            self._consecutive_failures = 0
            self._next_health_check_after = self._time_fn() + self._health_interval_seconds

    def _record_remote_success(self) -> None:
        """远端调用成功时清理失败计数。"""
        with self._lock:
            self._consecutive_failures = 0
            self._next_health_check_after = self._time_fn() + self._health_interval_seconds

    def _record_remote_failure(self, exc: Exception) -> None:
        """远端调用或健康检查失败后累计失败并按阈值熔断。"""
        with self._lock:
            self._consecutive_failures += 1
            failures = self._consecutive_failures
            should_open = failures >= self._failure_threshold

        if should_open:
            self._open_circuit(reason=f"failure-threshold ({failures}/{self._failure_threshold})")
        else:
            logger.warning(
                "远端 ASR 失败计数增加: endpoint=%s, failures=%d/%d, error=%s",
                self._remote_client.endpoint,
                failures,
                self._failure_threshold,
                exc,
            )

    def _handle_recovery_probe_success(self, health: dict[str, Any]) -> None:
        """冷却后恢复探测成功，按阈值决定是否恢复远端优先。"""
        now = self._time_fn()
        with self._lock:
            self._state = RemoteCircuitState.HALF_OPEN
            self._consecutive_recovery_successes += 1
            recovery_successes = self._consecutive_recovery_successes

            if recovery_successes >= self._recovery_threshold:
                self._state = RemoteCircuitState.CLOSED
                self._consecutive_failures = 0
                self._consecutive_recovery_successes = 0
                self._cooldown_until = 0.0
                self._next_recovery_probe_after = 0.0
                self._next_health_check_after = now + self._health_interval_seconds
                recovered = True
            else:
                self._cooldown_until = now
                self._next_recovery_probe_after = now + self._health_interval_seconds
                recovered = False

        if recovered:
            logger.info(
                "远端 ASR 恢复成功，重新切回远端优先: endpoint=%s, backend=%s, model=%s",
                self._remote_client.endpoint,
                health.get("backend", "unknown"),
                health.get("model_size", "unknown"),
            )
        else:
            logger.info(
                "远端 ASR 恢复探测成功，等待达到恢复阈值: endpoint=%s, successes=%d/%d",
                self._remote_client.endpoint,
                recovery_successes,
                self._recovery_threshold,
            )

    def _handle_recovery_probe_failure(self, exc: Exception) -> None:
        """恢复探测失败后重新进入冷却。"""
        self._open_circuit(reason="recovery-probe-failed")
        logger.warning("远端 ASR 恢复探测失败，继续本地 CPU 冷却回退: %s", exc)

    def _close_circuit(self) -> None:
        """切回 closed，恢复远端优先。"""
        with self._lock:
            now = self._time_fn()
            self._state = RemoteCircuitState.CLOSED
            self._consecutive_failures = 0
            self._consecutive_recovery_successes = 0
            self._cooldown_until = 0.0
            self._next_recovery_probe_after = 0.0
            self._next_health_check_after = now + self._health_interval_seconds

    def _open_circuit(self, reason: str) -> None:
        """切到 open，在冷却窗口内直接回退本地 CPU。"""
        with self._lock:
            now = self._time_fn()
            self._state = RemoteCircuitState.OPEN
            self._consecutive_failures = max(self._consecutive_failures, self._failure_threshold)
            self._consecutive_recovery_successes = 0
            self._cooldown_until = now + self._cooldown_seconds
            self._next_health_check_after = 0.0
            self._next_recovery_probe_after = self._cooldown_until

        logger.warning(
            "远端 ASR 已熔断: endpoint=%s, reason=%s, cooldown=%ss",
            self._remote_client.endpoint,
            reason,
            self._cooldown_seconds,
        )
