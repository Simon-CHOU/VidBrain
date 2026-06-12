"""
Draft Manager 模块。

在半自动模式下管理笔记草稿的生命周期：
1. write_draft  — Agent 生成的笔记先写入 _drafts/ 子目录
2. list_drafts  — 列出所有待审核草稿
3. publish_draft — 审核通过，移到正式 Vault
4. discard_draft — 审核拒绝，删除草稿

设计原则：
- 所有操作仅涉及 vault_dir 目录，保持 self-contained
- _drafts/ 是 vault_dir 的子目录，不创建额外系统目录
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from src.utils.frontmatter import parse_frontmatter

logger = logging.getLogger("vidbrain.drafts")

_DRAFTS_DIR = "_drafts"


def write_draft(vault_path: str, file_name: str, content: str, source_video: str) -> Path:
    """将笔记写入 _drafts/ 子目录。

    Args:
        vault_path: Vault 根目录路径
        file_name: 笔记文件名（不含路径）
        content: Markdown 正文内容（Agent 已含 front-matter 的输出会在此替换 front-matter）
        source_video: 源视频文件名（用于 front-matter）

    Returns:
        写入的草稿文件 Path
    """
    vp = Path(vault_path)
    drafts_dir = vp / _DRAFTS_DIR
    drafts_dir.mkdir(parents=True, exist_ok=True)

    draft_front_matter = (
        f"---\n"
        f"type: technical-note\n"
        f"source_video: {source_video}\n"
        f"status: draft\n"
        f"created: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"---\n\n"
    )
    # 如果 content 已含 front-matter（以 --- 开头），替换之
    _, body = parse_frontmatter(content)
    if body and body.strip():
        content = body.strip() + "\n"

    file_path = drafts_dir / file_name
    file_path.write_text(draft_front_matter + content, encoding="utf-8")
    logger.info("草稿已写入: %s", file_path)
    return file_path


def list_drafts(vault_path: str) -> list[str]:
    """列出 _drafts/ 目录下所有待审核草稿。

    Args:
        vault_path: Vault 根目录路径

    Returns:
        草稿文件名列表（不含路径前缀）
    """
    drafts_dir = Path(vault_path) / _DRAFTS_DIR
    if not drafts_dir.exists():
        return []
    return sorted([p.name for p in drafts_dir.glob("*.md")])


def publish_draft(vault_path: str, draft_name: str) -> Path | None:
    """将草稿从 _drafts/ 移动到 Vault 根目录，更新 front-matter。

    Args:
        vault_path: Vault 根目录路径
        draft_name: 草稿文件名

    Returns:
        发布后的文件 Path，如草稿不存在则返回 None
    """
    vp = Path(vault_path)
    src = vp / _DRAFTS_DIR / draft_name
    if not src.exists():
        logger.warning("草稿不存在: %s", src)
        return None

    content = src.read_text(encoding="utf-8", errors="replace")

    # 更新 front-matter：status: draft → status: auto-generated + reviewed fields
    reviewed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    metadata, body = parse_frontmatter(content)
    if metadata:
        metadata["status"] = "auto-generated"
        if "reviewed" not in metadata:
            metadata["reviewed"] = True
            metadata["reviewed_at"] = reviewed_at
        # 重建 front-matter 字符串
        fm_lines = ["---"]
        for k, v in metadata.items():
            if isinstance(v, bool):
                fm_lines.append(f"{k}: {str(v).lower()}")
            else:
                fm_lines.append(f"{k}: {v}")
        fm_lines.append("---")
        content = "\n".join(fm_lines) + "\n" + body

    dst = vp / draft_name
    # 如果正式目录已有同名文件，跳过移动（保留草稿）
    if dst.exists():
        logger.warning("发布目标已存在，跳过: %s", dst)
        return None

    # 使用 rename 移动（同磁盘快速移动）
    src.rename(dst)
    # 最终写入更新后的 frontmatter
    dst.write_text(content, encoding="utf-8")
    logger.info("草稿已发布: %s -> %s", draft_name, dst)
    return dst


def discard_draft(vault_path: str, draft_name: str) -> bool:
    """删除草稿文件。

    Args:
        vault_path: Vault 根目录路径
        draft_name: 草稿文件名

    Returns:
        是否成功删除
    """
    src = Path(vault_path) / _DRAFTS_DIR / draft_name
    if not src.exists():
        logger.warning("草稿不存在: %s", src)
        return False
    src.unlink()
    logger.info("草稿已删除: %s", draft_name)
    return True
