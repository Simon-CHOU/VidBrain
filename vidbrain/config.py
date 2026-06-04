"""
配置管理模块。

安全规则（重要）：
- DEEPSEEK_API_KEY 和 DEEPSEEK_BASE_URL 从 Windows 系统环境变量读取
- 不写入任何文件（不在 .env、不在日志、不在源码中）
- 日志中不得输出 API Key 的任何部分
- LLM 配置不可通过 CLI 参数覆盖（防止意外泄露）

自包含设计：
- 所有运行时产物（数据库、日志、模型缓存）均在项目目录内
- 项目外路径仅限：I:/web-videos（只读输入）、--vault-dir（知识库输出）
"""

from __future__ import annotations

import os
import multiprocessing
from pathlib import Path
from dataclasses import dataclass, field

# ── 确保模型缓存位于项目内部 ──
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_MODEL_CACHE = str(_PROJECT_ROOT / ".model_cache")
os.environ.setdefault("HF_HOME", _MODEL_CACHE)
os.environ.setdefault("HF_HUB_CACHE", _MODEL_CACHE)


@dataclass
class LLMConfig:
    """LLM 配置，仅从系统环境变量读取。"""

    api_key: str = field(init=False)
    base_url: str = field(init=False)
    model: str = "deepseek-v4-flash"

    def __post_init__(self) -> None:
        key = os.environ.get("DEEPSEEK_API_KEY", "")
        url = os.environ.get("DEEPSEEK_BASE_URL", "")
        if not key:
            raise OSError(
                "环境变量 DEEPSEEK_API_KEY 未设置。"
                "请通过 Windows 系统环境变量设置。"
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

    input_dir: str = r"I:\web-videos"
    vault_dir: str = ""
    db_path: str = "./pipeline.db"
    model_size: str = "large-v3"
    cpu_threads: int = field(default_factory=lambda: max(1, multiprocessing.cpu_count() - 1))
    once: bool = False
    limit: int = 0  # 0 = 不限制
    batch_size: int = 5  # 每批处理的视频数
    interval_seconds: int = 0  # 持续模式的间隔秒数（0 = 不启用）
    classify_only: bool = False  # 仅分类，不处理
    refine: bool = False  # 执行知识库精炼
