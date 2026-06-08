"""
视频文件名分类器。

基于关键词匹配对视频文件名进行分类，避免对无关视频执行 ASR + API 调用。
"""

from __future__ import annotations

import logging
import re
from typing import Tuple

logger = logging.getLogger("vidbrain.classifier")

# ── 关键词白名单（命中任意即分类为 tech） ──
_TECH_KEYWORDS = [
    # 技术类
    "编程",
    "Python",
    "Go",
    "Rust",
    "算法",
    "架构",
    "源码",
    "设计模式",
    "区块链",
    "Web3",
    "智能合约",
    "Solidity",
    "EVM",
    "DeFi",
    "NFT",
    "AI",
    "LLM",
    "大模型",
    "机器学习",
    "深度学习",
    "神经网络",
    "Kubernetes",
    "Docker",
    "Linux",
    "数据库",
    "后端",
    "前端",
    "DevOps",
    "面试",
    "面经",
    "LeetCode",
    "刷题",
    "程序员",
    "GPU",
    "CUDA",
    "算子",
    "推理",
    "训练",
    "模型",
    "微服务",
    "分布式",
    "高并发",
    "性能优化",
    "系统设计",
    "Git",
    "API",
    "REST",
    "gRPC",
    "协议",
    "Java",
    "Redis",
    "JVM",
    "Spring",
    "C语言",
    "C++",
    "JavaScript",
    "TypeScript",
    # 知识类
    "科普",
    "原理",
    "深度",
    "解析",
    "拆解",
    "底层",
    "认知",
    "方法论",
    "思维",
    "经济",
    "物理",
    "数学",
    "科学",
    "技术",
    "创业",
    "复盘",
    "演讲",
    "讲座",
    "代码",
    "软件",
    # 课程类
    "教程",
    "课程",
    "教学",
    "入门",
    "进阶",
    "实战",
    "训练营",
    "公开课",
    # 观点类
    "观点",
    "思辨",
    "评论",
    "分析",
    "讨论",
    "对话",
    "对谈",
    "播客",
    "访谈",
    "圆桌",
    "辩论",
    "座谈",
    "对讲",
    # 公司与产品
    "谷歌",
    "Google",
    "Apple",
    "微软",
    "Meta",
    "Amazon",
    "AWS",
    "iPhone",
    "Mac",
    "Windows",
]

# ── 关键词黑名单（命中任意即跳过） ──
_SKIP_KEYWORDS = [
    "抖音",
    "快手",
    "搞笑",
    "娱乐",
    "日常",
    "vlog",
    "吃播",
    "带货",
    "美女",
    "小姐姐",
    "漫展",
    "cos",
    "COS",
    "Coser",
    "舞蹈",
    "翻唱",
    "音乐",
    "写真",
    "模特",
    "泳装",
    "内衣",
]


def classify_video(filename: str) -> Tuple[str, str]:
    """对视频文件名进行分类。

    Returns:
        (category, reason):
            category: "tech" | "skip" | "unclear"
            reason:   分类理由简述
    """
    name_lower = filename.lower()

    # 1. 黑名单优先
    for kw in _SKIP_KEYWORDS:
        if kw.lower() in name_lower:
            return "skip", f"文件名含黑名单关键词: {kw}"

    # 2. 白名单
    for kw in _TECH_KEYWORDS:
        if kw.lower() in name_lower:
            return "tech", f"文件名含技术/知识关键词: {kw}"

    # 3. 含 @ 后跟英文 — 可能是技术博主（排除纯中文@名）
    at_match = re.search(r"@([a-zA-Z][a-zA-Z0-9_.]{2,})", filename)
    if at_match:
        return "tech", f"文件名含技术博主标识: @{at_match.group(1)}"

    # 4. 兜底
    return "unclear", "未匹配任何已知规则，暂不处理"
