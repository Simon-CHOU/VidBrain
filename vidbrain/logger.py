"""
日志系统。

统一日志输出，自动脱敏敏感信息：
- 对包含 key / secret / token / sk 的字段值进行 ***MASKED*** 替换
"""

from __future__ import annotations

import logging
import os
import re
from logging.handlers import RotatingFileHandler

_SENSITIVE_PATTERN = re.compile(
    r"(?i)(api_key|api_secret|access_token|secret_key)\s*[=:]\s*['\"]?\S+?['\"]?"
)


def _mask_sensitive(msg: str) -> str:
    """脱敏处理：替换所有敏感字段值为 ***MASKED***。"""
    return _SENSITIVE_PATTERN.sub(r"\1 = ***MASKED***", msg)


class SensitiveDataFilter(logging.Filter):
    """日志过滤器，在输出前对敏感数据进行脱敏。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _mask_sensitive(record.msg)
        # Also sanitize args if present
        if record.args:
            sanitized = tuple(
                _mask_sensitive(str(a)) if isinstance(a, str) else a
                for a in record.args
            )
            record.args = sanitized
        return True


def setup_logger(name: str = "vidbrain", log_dir: str = "logs") -> logging.Logger:
    """配置并返回 logger 实例。

    - 控制台输出：INFO 级别
    - 文件输出：DEBUG 级别，写入 {log_dir}/vidbrain.log
    - 日志轮转：10MB，保留 3 个备份
    - 自动脱敏所有敏感字段
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    # 统一格式
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(SensitiveDataFilter())
    logger.addHandler(console_handler)

    # 文件处理器（带轮转）
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "vidbrain.log")
    file_handler = RotatingFileHandler(
        log_path, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(SensitiveDataFilter())
    logger.addHandler(file_handler)

    return logger
