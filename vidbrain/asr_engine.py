"""
ASR 引擎封装。

将 faster-whisper 封装为全局单例，避免重复加载 ~3GB 模型。
"""

from __future__ import annotations

import logging
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
        """延迟加载模型（仅首次调用时加载）。"""
        if ASREngine._model is None:
            logger.info(
                "加载 Whisper 模型: size=%s, device=%s, compute_type=%s, cpu_threads=%d",
                self._model_size,
                self._device,
                self._compute_type,
                self._cpu_threads,
            )
            ASREngine._model = WhisperModel(
                self._model_size,
                device=self._device,
                compute_type=self._compute_type,
                cpu_threads=self._cpu_threads,
            )
        return ASREngine._model

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
