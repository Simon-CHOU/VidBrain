"""
ASR 引擎封装。

将 faster-whisper 封装为全局单例，避免重复加载 ~3GB 模型。
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from faster_whisper import WhisperModel

logger = logging.getLogger("vidbrain.asr_engine")


class ASREngine:
    """faster-whisper 引擎封装（全局单例）。"""

    _model: WhisperModel | None = None

    def __init__(
        self,
        model_size: str = "large-v3",
        device: str = "cpu",
        compute_type: str = "int8",
        cpu_threads: int = 4,
    ) -> None:
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._cpu_threads = cpu_threads

    def _get_model(self) -> WhisperModel:
        """延迟加载模型（仅首次调用时加载），含重试逻辑与 HF 镜像支持。"""
        if ASREngine._model is not None:
            return ASREngine._model

        hf_endpoint = os.environ.get("HF_ENDPOINT")
        if hf_endpoint:
            logger.info("使用 HF 镜像: %s", hf_endpoint)

        logger.info(
            "加载 Whisper 模型: size=%s, device=%s, compute_type=%s, cpu_threads=%d",
            self._model_size,
            self._device,
            self._compute_type,
            self._cpu_threads,
        )

        last_exception: Exception | None = None
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                ASREngine._model = WhisperModel(
                    self._model_size,
                    device=self._device,
                    compute_type=self._compute_type,
                    cpu_threads=self._cpu_threads,
                )
                logger.info("模型加载成功 (第 %d 次尝试)", attempt)
                return ASREngine._model
            except Exception as exc:
                last_exception = exc
                if attempt < max_attempts:
                    delay = 2 ** (attempt - 1)
                    logger.warning(
                        "模型下载失败 (第 %d/%d 次): %s，%ds 后重试...",
                        attempt, max_attempts, exc, delay,
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        "模型下载最终失败 (第 %d/%d 次): %s",
                        attempt, max_attempts, exc,
                    )

        raise RuntimeError(
            f"无法加载 Whisper 模型 '{self._model_size}'，经过 {max_attempts} 次尝试后失败。"
            f" 请检查网络连接，或设置 HF_ENDPOINT 环境变量以使用镜像站点。"
            f" 最后错误: {last_exception}"
        )

    @classmethod
    def prepare_model(cls, model_size: str = "large-v3", cpu_threads: int = 4) -> WhisperModel:
        """预下载模型（在启动时调用以在首条任务之前完成下载）。

        会直接触发 HuggingFace 模型下载，因而不依赖实例的 device/compute_type 设置，
        固定使用 device="cpu" + compute_type="int8" 以便在任意环境下运行。
        """
        logger.info(
            "预下载模型: size=%s, device=cpu, compute_type=int8, cpu_threads=%d",
            model_size, cpu_threads,
        )
        model = WhisperModel(
            model_size,
            device="cpu",
            compute_type="int8",
            cpu_threads=cpu_threads,
        )
        logger.info("模型预下载完成: %s", model_size)
        return model

    def transcribe(self, file_path: str) -> list[dict[str, Any]]:
        """执行语音转录，返回带时间戳的文本段列表。

        返回格式：[{"start": 0.0, "end": 2.5, "text": "..."}, ...]
        """
        logger.info("开始转录: %s", file_path)
        model = self._get_model()
        segments, _info = model.transcribe(file_path, beam_size=5, vad_filter=True)

        results: list[dict[str, Any]] = []
        for segment in segments:
            results.append(
                {
                    "start": round(segment.start, 2),
                    "end": round(segment.end, 2),
                    "text": segment.text.strip(),
                }
            )

        logger.info("转录完成: %s, 共 %d 段", file_path, len(results))
        return results
