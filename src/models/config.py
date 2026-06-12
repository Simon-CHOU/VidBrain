"""
配置管理模块。

安全规则（重要）：
- DEEPSEEK_API_KEY 和 DEEPSEEK_BASE_URL 从 Windows 系统环境变量读取
- 不写入任何文件（不在 .env、不在日志、不在源码中）
- 日志中不得输出 API Key 的任何部分
- LLM 配置不可通过 CLI 参数覆盖（防止意外泄露）

自包含设计：
- 所有运行时产物（数据库、日志、模型缓存）均在项目目录内
- 项目外路径仅限 --input-dir（只读输入）、--vault-dir（知识库输出）
"""

from __future__ import annotations

import multiprocessing
import os
from dataclasses import dataclass, field
from pathlib import Path

# ── 确保模型缓存位于项目内部 ──
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_MODEL_CACHE = str(_PROJECT_ROOT / ".model_cache")


def setup_environment() -> None:
    """设置 HF 缓存路径和镜像，应在 main() 中调用而非 import 时执行。"""
    os.environ.setdefault("HF_HOME", _MODEL_CACHE)
    os.environ.setdefault("HF_HUB_CACHE", _MODEL_CACHE)
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")


@dataclass
class LLMConfig:
    """LLM 配置，仅从系统环境变量读取。"""

    api_key: str = field(init=False)
    base_url: str = field(init=False)
    model: str = "deepseek-v4-flash"

    def __post_init__(self) -> None:
        """从系统环境变量加载 API Key 和 Base URL。

        Raises:
            OSError: 环境变量未设置时抛出。
        """
        key = os.environ.get("DEEPSEEK_API_KEY", "")
        url = os.environ.get("DEEPSEEK_BASE_URL", "")
        if not key:
            raise OSError(
                "环境变量 DEEPSEEK_API_KEY 未设置。" "请通过 Windows 系统环境变量设置。"
            )
        if not url:
            raise OSError(
                "环境变量 DEEPSEEK_BASE_URL 未设置。"
                "请通过 Windows 系统环境变量设置。"
            )
        self.api_key = key
        self.base_url = url


@dataclass
class PipelineConfig:
    """管线配置，通过 CLI 参数传入。

    重要约束：程序永远不得修改 input_dir 下的任何文件（增删改）。
    """

    input_dir: str = r"I:\web-videos"  # 默认输入目录
    vault_dir: str = "./vidbrain_vault"
    db_path: str = "./pipeline.db"
    model_size: str = "tiny"  # 统一默认值：桌面友好，可按需用 --model-size 覆盖
    cpu_threads: int = field(
        default_factory=lambda: max(1, multiprocessing.cpu_count() - 1)
    )
    once: bool = False
    limit: int = 0
    batch_size: int = 10
    interval_seconds: int = 0
    classify_only: bool = False
    refine: bool = False
    auto_refine_after: int = 0
    auto_refine_every_hours: int = 0
    retry_failed: bool = False
    semi: bool = False
    review_drafts: bool = False
    review_classifications: bool = False
    priority_level: str = "normal"
    video_cooldown: int = 0
    embedding_enabled: bool = False
    parallel_workers: int = 0
    asr_backend: str = "cpu"
    role: str = "primary"
    remote_asr_host: str = ""
    remote_asr_port: int = 8080
    remote_asr_timeout_seconds: float = 2.0
    remote_asr_health_interval_seconds: int = 10
    remote_asr_failure_threshold: int = 2
    remote_asr_recovery_threshold: int = 2
    remote_asr_cooldown_seconds: int = 60
    profile: str = "auto"
    continuous: bool = False


@dataclass
class EmbeddingConfig:
    """Embedding API 配置，仅从系统环境变量读取。"""

    api_key: str = field(init=False)
    base_url: str = field(init=False)
    model: str = "text-embedding-v4"

    def __post_init__(self) -> None:
        """从系统环境变量加载 DashScope API Key。

        Raises:
            OSError: 环境变量未设置时抛出。
        """
        key = os.environ.get("DASHSCOPE_API_KEY", "")
        url = os.environ.get(
            "DASHSCOPE_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        if not key:
            raise OSError(
                "环境变量 DASHSCOPE_API_KEY 未设置。"
                "请通过 Windows 系统环境变量设置。"
            )
        self.api_key = key
        self.base_url = url
