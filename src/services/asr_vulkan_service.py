"""
ASR 引擎 - whisper.cpp Vulkan 后端。

通过 subprocess 调用 whisper.cpp CLI 进行 GPU 加速语音识别。
模型使用 GGML 格式（与 faster-whisper 的 HuggingFace 格式不同，需单独下载）。

降级策略：
- 如果 whisper-cli 不可用或 Vulkan 初始化失败，自动回退到 faster-whisper CPU
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger("vidbrain.asr_engine_vulkan")

# ── 默认路径 ──
_DEFAULT_WHISPER_CLI = "whisper-cli.exe"
_DEFAULT_MODEL_DIR = str(Path(__file__).resolve().parent.parent / ".model_cache" / "ggml")

# GGML 模型名称映射（与 faster-whisper 的 model_size 对应）
_MODEL_NAMES: dict[str, str] = {
    "tiny": "ggml-tiny.bin",
    "tiny.en": "ggml-tiny.en.bin",
    "base": "ggml-base.bin",
    "base.en": "ggml-base.en.bin",
    "small": "ggml-small.bin",
    "small.en": "ggml-small.en.bin",
    "medium": "ggml-medium.bin",
    "medium.en": "ggml-medium.en.bin",
    "large-v1": "ggml-large-v1.bin",
    "large-v2": "ggml-large-v2.bin",
    "large-v3": "ggml-large-v3.bin",
    "large": "ggml-large-v3.bin",
}


def _find_whisper_cli() -> str | None:
    """查找 whisper-cli 可执行文件。

    搜索顺序：
    1. 环境变量 WHISPER_CLI_PATH
    2. 项目目录下的 tools/whisper-cli.exe
    3. 项目目录下的 whisper.cpp/build/bin/Release/whisper-cli.exe
    4. PATH 中的 whisper-cli.exe
    """
    # 环境变量
    env_path = os.environ.get("WHISPER_CLI_PATH")
    if env_path and Path(env_path).is_file():
        return env_path

    # 项目目录
    project_root = Path(__file__).resolve().parent.parent
    candidates = [
        project_root / "tools" / "whisper-cli.exe",
        project_root / "tools" / "whisper.cpp" / "build" / "bin" / "Release" / "whisper-cli.exe",
        project_root / "whisper.cpp" / "build" / "bin" / "Release" / "whisper-cli.exe",
    ]
    for p in candidates:
        if p.is_file():
            return str(p)

    # PATH
    found = shutil.which("whisper-cli")
    if found:
        return found

    return None


def _find_ggml_model(model_size: str, model_dir: str | None = None) -> str | None:
    """查找 GGML 格式的 Whisper 模型文件。"""
    filename = _MODEL_NAMES.get(model_size)
    if not filename:
        return None

    search_dirs = []
    if model_dir:
        search_dirs.append(Path(model_dir))
    search_dirs.append(Path(_DEFAULT_MODEL_DIR))

    # 也搜索 faster-whisper 模型缓存旁边
    cache_root = Path(__file__).resolve().parent.parent / ".model_cache"
    for subdir in cache_root.glob("models--*"):
        ggml_dir = subdir / "ggml"
        if ggml_dir.is_dir():
            search_dirs.append(ggml_dir)

    for d in search_dirs:
        candidate = d / filename
        if candidate.is_file():
            return str(candidate)

    return None


def _extract_audio(file_path: str, output_path: str | None = None) -> str:
    """使用 ffmpeg 从视频提取 16kHz 单声道 WAV 音频。

    如果系统 ffmpeg 支持 Vulkan，可添加硬件加速参数。
    返回 WAV 文件路径。
    """
    if output_path is None:
        fd, output_path = tempfile.mkstemp(suffix=".wav", prefix="vidbrain_audio_")
        os.close(fd)

    # 尝试 Vulkan 加速（如果可用），静默失败回退到纯 CPU
    cmd_vulkan = [
        "ffmpeg",
        "-y",
        "-hwaccel",
        "vulkan",
        "-i",
        file_path,
        "-acodec",
        "pcm_s16le",
        "-ac",
        "1",
        "-ar",
        "16000",
        output_path,
    ]
    cmd_cpu = [
        "ffmpeg",
        "-y",
        "-i",
        file_path,
        "-acodec",
        "pcm_s16le",
        "-ac",
        "1",
        "-ar",
        "16000",
        output_path,
    ]

    for attempt, cmd in enumerate([cmd_vulkan, cmd_cpu]):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0 and Path(output_path).is_file():
                if attempt == 0:
                    logger.debug("音频提取 (Vulkan 加速): %s", file_path)
                return output_path
        except (subprocess.TimeoutExpired, FileNotFoundError):
            if attempt == 0:
                logger.debug("Vulkan ffmpeg 不可用，回退到 CPU 音频提取")
            continue

    raise RuntimeError(f"ffmpeg 音频提取失败: {file_path}")


class ASREngineVulkan:
    """whisper.cpp Vulkan ASR 引擎。

    使用 whisper.cpp 的 Vulkan 后端进行 GPU 加速语音识别。
    如果 Vulkan 不可用（GPU 不支持或驱动问题），自动回退到
    faster-whisper CPU 路径。
    """

    def __init__(
        self,
        model_size: str = "small",
        cpu_threads: int = 4,
        whisper_cli_path: str | None = None,
        model_dir: str | None = None,
        language: str = "zh",
    ) -> None:
        self._model_size = model_size
        self._cpu_threads = cpu_threads
        self._language = language
        self._vulkan_available: bool | None = None  # None = 未检测

        # 定位 whisper-cli
        self._whisper_cli = whisper_cli_path or _find_whisper_cli()
        if self._whisper_cli:
            logger.info("找到 whisper-cli: %s", self._whisper_cli)
        else:
            logger.warning(
                "未找到 whisper-cli，将自动回退到 faster-whisper CPU。"
                "设置 WHISPER_CLI_PATH 环境变量或编译 whisper.cpp 启用 Vulkan 加速。"
            )

        # 定位 GGML 模型
        self._model_path = _find_ggml_model(model_size, model_dir)
        if self._model_path:
            logger.info("找到 GGML 模型: %s", self._model_path)
        else:
            logger.info(
                "未找到 GGML 模型 (%s)，请下载到 %s/ 目录。"
                "使用命令: whisper.cpp/models/download-ggml-model.cmd %s",
                _MODEL_NAMES.get(model_size, model_size),
                _DEFAULT_MODEL_DIR,
                model_size,
            )

        # 备份的 faster-whisper CPU 引擎（延迟加载）
        self._cpu_fallback = None

    # ── 能力检测 ──

    @property
    def vulkan_available(self) -> bool:
        """检测 Vulkan 是否可用（缓存结果）。"""
        if self._vulkan_available is not None:
            return self._vulkan_available

        if not self._whisper_cli or not self._model_path:
            self._vulkan_available = False
            return False

        # 测试 Vulkan 初始化
        try:
            result = subprocess.run(
                [self._whisper_cli, "--help"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            # 检查是否有 vulkan 相关信息
            self._vulkan_available = result.returncode == 0
        except Exception:
            self._vulkan_available = False

        logger.info(
            "Vulkan ASR %s",
            "可用" if self._vulkan_available else "不可用，回退到 CPU",
        )
        return self._vulkan_available

    # ── 转录 ──

    def transcribe(self, file_path: str) -> list[dict[str, Any]]:
        """执行语音转录，返回带时间戳的文本段列表。

        优先使用 Vulkan 加速，不可用时自动回退到 CPU。
        """
        if self.vulkan_available:
            return self._transcribe_vulkan(file_path)
        else:
            return self._transcribe_cpu_fallback(file_path)

    def _transcribe_vulkan(self, file_path: str) -> list[dict[str, Any]]:
        """使用 whisper.cpp Vulkan 后端进行转录。"""
        logger.info("[Vulkan] 开始转录: %s", file_path)

        # Step 1: 音频提取
        audio_path = _extract_audio(file_path)

        try:
            # Step 2: whisper.cpp 推理
            cmd = [
                self._whisper_cli,
                "-m",
                self._model_path,
                "-f",
                audio_path,
                "-l",
                self._language,
                "-t",
                str(self._cpu_threads),
                "--output-json",
                "--print-progress",
            ]

            logger.debug("[Vulkan] 执行: %s", " ".join(cmd))
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,  # 10 分钟超时
            )

            if result.returncode != 0:
                raise RuntimeError(
                    f"whisper-cli 返回非零退出码 {result.returncode}: {result.stderr[:500]}"
                )

            # Step 3: 解析 JSON 输出
            return self._parse_whisper_output(result.stdout)

        except (subprocess.TimeoutExpired, RuntimeError) as e:
            logger.warning("[Vulkan] 转录失败: %s，回退到 CPU", str(e))
            return self._transcribe_cpu_fallback(file_path)

        finally:
            # 清理临时音频文件
            try:
                os.unlink(audio_path)
            except OSError:
                pass

    def _parse_whisper_output(self, stdout: str) -> list[dict[str, Any]]:
        """解析 whisper.cpp --output-json 的输出。

        输出格式:
        {
          "transcription": [
            {
              "timestamps": {"from": "00:00:00,000", "to": "00:00:05,000"},
              "offsets": {"from": 0, "to": 5000},
              "text": "..."
            }
          ]
        }

        转换为:
        [{"start": 0.0, "end": 5.0, "text": "..."}, ...]
        """
        # whisper.cpp 的 JSON 输出可能在多行中（带进度信息）
        # 提取 JSON 块
        json_start = stdout.find("{")
        json_end = stdout.rfind("}") + 1
        if json_start == -1 or json_end <= json_start:
            # 尝试直接解析
            json_str = stdout.strip()
        else:
            json_str = stdout[json_start:json_end]

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            # 如果 JSON 解析失败，尝试提取最后一行的 JSON
            lines = stdout.strip().split("\n")
            for line in reversed(lines):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        data = json.loads(line)
                        break
                    except json.JSONDecodeError:
                        continue
            else:
                raise RuntimeError(f"无法解析 whisper.cpp 输出: {stdout[:500]}")

        transcription = data.get("transcription", [])
        results: list[dict[str, Any]] = []
        for seg in transcription:
            offsets = seg.get("offsets", {})
            start_ms = offsets.get("from", 0)
            end_ms = offsets.get("to", 0)
            results.append(
                {
                    "start": round(start_ms / 1000.0, 2),
                    "end": round(end_ms / 1000.0, 2),
                    "text": seg.get("text", "").strip(),
                }
            )

        logger.info("[Vulkan] 转录完成: %d 段", len(results))
        return results

    def _transcribe_cpu_fallback(self, file_path: str) -> list[dict[str, Any]]:
        """回退到 faster-whisper CPU 转录。"""
        if self._cpu_fallback is None:
            from src.services.asr_service import ASREngine

            self._cpu_fallback = ASREngine(
                model_size=self._model_size,
                device="cpu",
                compute_type="int8",
                cpu_threads=self._cpu_threads,
            )
        logger.info("[Fallback] 使用 faster-whisper CPU 转录: %s", file_path)
        return self._cpu_fallback.transcribe(file_path)

    # ── 模型预热（兼容现有 prepare_model 接口） ──

    @classmethod
    def prepare_model(
        cls,
        model_size: str = "small",
        cpu_threads: int = 4,
        prefer_vulkan: bool = True,
    ) -> ASREngineVulkan:
        """预检查和准备模型，返回引擎实例。"""
        engine = cls(model_size=model_size, cpu_threads=cpu_threads)

        if prefer_vulkan and engine.vulkan_available:
            logger.info("Vulkan ASR 已就绪: model=%s", model_size)
        else:
            logger.info("CPU ASR 已就绪 (Vulkan 不可用): model=%s", model_size)

        return engine
