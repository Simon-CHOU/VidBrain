"""
ASR 引擎封装。

将 faster-whisper 封装为全局单例，避免重复加载 ~3GB 模型。
核心改进：优先使用本地已缓存模型，彻底避免每次启动都请求 HuggingFace。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from faster_whisper import WhisperModel

logger = logging.getLogger("vidbrain.asr_engine")

# faster-whisper 的模型名 → HuggingFace repo_id 映射
_MODEL_REPOS = {
    "tiny": "Systran/faster-whisper-tiny",
    "tiny.en": "Systran/faster-whisper-tiny.en",
    "base": "Systran/faster-whisper-base",
    "base.en": "Systran/faster-whisper-base.en",
    "small": "Systran/faster-whisper-small",
    "small.en": "Systran/faster-whisper-small.en",
    "medium": "Systran/faster-whisper-medium",
    "medium.en": "Systran/faster-whisper-medium.en",
    "large-v1": "Systran/faster-whisper-large-v1",
    "large-v2": "Systran/faster-whisper-large-v2",
    "large-v3": "Systran/faster-whisper-large-v3",
    "large": "Systran/faster-whisper-large-v3",
}


def _repo_to_cache_dir(repo_id: str) -> str:
    """将 HuggingFace repo_id 转换为本地缓存目录名。

    huggingface_hub 的存储规则：{cache_dir}/models--{org}--{repo}/
    """
    return repo_id.replace("/", "--")


def _find_cached_snapshot(model_size: str) -> str | None:
    """在本地缓存中查找已下载的模型快照目录。

    如果模型已完整下载到 HF_HUB_CACHE 中，则直接返回快照目录路径，
    这样 WhisperModel 可以直接加载本地文件，完全跳过网络请求。

    Returns:
        快照目录路径，如果未找到则返回 None。
    """
    repo_id = _MODEL_REPOS.get(model_size)
    if repo_id is None:
        return None

    cache_dir = os.environ.get("HF_HUB_CACHE") or os.environ.get("HF_HOME")
    if not cache_dir:
        return None

    model_cache_dir = Path(cache_dir) / f"models--{_repo_to_cache_dir(repo_id)}"
    snapshots_dir = model_cache_dir / "snapshots"

    if not snapshots_dir.is_dir():
        return None

    # 找一个包含 model.bin 的快照（可能存在多个 revision）
    required_files = {"model.bin", "config.json", "tokenizer.json"}
    for snapshot in sorted(snapshots_dir.iterdir(), reverse=True):
        if not snapshot.is_dir():
            continue
        existing = {f.name for f in snapshot.iterdir() if f.is_file()}
        if required_files.issubset(existing):
            return str(snapshot)

    return None


class ASREngine:
    """faster-whisper 引擎封装（全局单例）。"""

    _model: WhisperModel | None = None
    _model_lock = threading.Lock()  # 保护 _model 的懒加载和 prepare_model

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

    def _load_model(self, model_size: str, is_retry: bool = False) -> WhisperModel:
        """加载 Whisper 模型，优先使用本地缓存。

        策略：
        1. 先查找本地缓存快照，如果存在则直接加载（零网络请求）
        2. 如果本地没有，尝试通过 HF 镜像下载
        3. 支持重试
        """
        # 策略 1: 本地缓存优先
        cached_path = _find_cached_snapshot(model_size)
        if cached_path:
            logger.info(
                "使用本地缓存模型: %s -> %s",
                model_size,
                cached_path,
            )
            try:
                model = WhisperModel(
                    cached_path,  # 直接传本地路径，跳过下载
                    device=self._device,
                    compute_type=self._compute_type,
                    cpu_threads=self._cpu_threads,
                    local_files_only=True,
                )
                logger.info("本地模型加载成功: %s", model_size)
                return model
            except Exception:
                logger.warning(
                    "本地缓存模型加载失败，尝试网络下载: %s",
                    model_size,
                )

        # 策略 2: 网络下载（带重试）
        hf_endpoint = os.environ.get("HF_ENDPOINT")
        if hf_endpoint:
            logger.info("使用 HF 镜像: %s", hf_endpoint)

        logger.info(
            "加载 Whisper 模型: size=%s, device=%s, compute_type=%s, cpu_threads=%d",
            model_size,
            self._device,
            self._compute_type,
            self._cpu_threads,
        )

        last_exception: Exception | None = None
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                # 从缓存加载（但允许联网验证)或从网络下载
                model = WhisperModel(
                    model_size,
                    device=self._device,
                    compute_type=self._compute_type,
                    cpu_threads=self._cpu_threads,
                    local_files_only=is_retry,
                )
                logger.info("模型加载成功 (第 %d 次尝试)", attempt)
                return model
            except Exception as exc:
                last_exception = exc
                if attempt < max_attempts:
                    delay = 2 ** (attempt - 1)
                    logger.warning(
                        "模型下载失败 (第 %d/%d 次): %s，%ds 后重试...",
                        attempt,
                        max_attempts,
                        exc,
                        delay,
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        "模型下载最终失败 (第 %d/%d 次): %s",
                        attempt,
                        max_attempts,
                        exc,
                    )

        raise RuntimeError(
            f"无法加载 Whisper 模型 '{model_size}'，经过 {max_attempts} 次尝试后失败。"
            f" 请检查网络连接，或设置 HF_ENDPOINT 环境变量以使用镜像站点。"
            f" 最后错误: {last_exception}"
        )

    def _get_model(self) -> WhisperModel:
        """延迟加载模型（仅首次调用时加载），线程安全。"""
        if ASREngine._model is not None:
            return ASREngine._model

        with ASREngine._model_lock:
            # 双重检查：可能在等待锁期间已被其他线程加载
            if ASREngine._model is not None:
                return ASREngine._model
            ASREngine._model = self._load_model(self._model_size)
            return ASREngine._model

    @classmethod
    def prepare_model(cls, model_size: str = "large-v3", cpu_threads: int = 4) -> WhisperModel:
        """预下载模型（在启动时调用以在首条任务之前完成下载）。

        优先使用本地缓存；如果本地已缓存则零网络请求完成加载。
        加载后会存入类级别 _model 单例，后续 _get_model() 直接复用。
        线程安全。
        """
        # 快速路径：已加载
        if cls._model is not None:
            return cls._model

        with cls._model_lock:
            if cls._model is not None:
                return cls._model

            logger.info(
                "预下载模型: size=%s, device=cpu, compute_type=int8, cpu_threads=%d",
                model_size,
                cpu_threads,
            )

            # 先尝试从本地缓存加载
            cached_path = _find_cached_snapshot(model_size)
            if cached_path:
                logger.info("使用本地缓存模型: %s -> %s", model_size, cached_path)
                try:
                    cls._model = WhisperModel(
                        cached_path,
                        device="cpu",
                        compute_type="int8",
                        cpu_threads=cpu_threads,
                        local_files_only=True,
                    )
                    logger.info("模型预下载完成（本地缓存）: %s", model_size)
                    return cls._model
                except Exception:
                    logger.warning("本地缓存模型加载失败，尝试网络下载: %s", model_size)

            # 本地缓存不可用，通过网络下载
            hf_endpoint = os.environ.get("HF_ENDPOINT")
            if hf_endpoint:
                logger.info("使用 HF 镜像: %s", hf_endpoint)

            cls._model = WhisperModel(
                model_size,
                device="cpu",
                compute_type="int8",
                cpu_threads=cpu_threads,
            )
            logger.info("模型预下载完成: %s", model_size)
            return cls._model

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
