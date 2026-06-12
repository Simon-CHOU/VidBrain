"""
用户反馈闭环模块。

通过扫描 Obsidian Vault 中用户对自动生成笔记的编辑行为，
提取反馈信号，用于优化后续 Agent 的处理策略。

设计原则：
- 只读取 vault_dir 目录下的 .md 文件
- 不修改任何文件
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from pathlib import Path

from src.utils.frontmatter import parse_frontmatter as _parse_frontmatter

logger = logging.getLogger("vidbrain.feedback")

# ── 笔记文件名前缀 ──
_MOC_PREFIX = "MOC-"

# ── 时间格式 ──
_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# ── 编辑时间缓冲（秒）──
_EDIT_BUFFER_SECONDS = 60


def parse_front_matter(content: str) -> dict:
    """从 Markdown 文本中提取 YAML front-matter。

    解析 `---` 分隔符之间的键值对，返回包含已知字段的字典。
    字段：type, status, source_video, created, reviewed, reviewed_at, quality

    Args:
        content: Markdown 文本全文。

    Returns:
        解析出的 front-matter 字典。如果 front-matter 不存在或无效，返回空 dict。
    """
    metadata, _body = _parse_frontmatter(content)
    return metadata


def _parse_created_time(fm: dict) -> datetime | None:
    """从 front-matter 中解析 created 时间戳。"""
    created_str = fm.get("created", "")
    if not created_str or not isinstance(created_str, str):
        return None
    for fmt in (_TIME_FORMAT, "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(created_str, fmt)
        except ValueError:
            continue
    return None


def detect_user_edits(vault_path: str) -> list[dict]:  # noqa: C901
    """扫描 Vault 中用户编辑过的笔记。

    对每篇非草稿、非 MOC 的 markdown 笔记：
    1. 解析 front-matter 获取 status 和 created 时间戳
    2. 比较文件 mtime 与 created 时间：如果 mtime > created + 60 秒，
       标记为 user_edited
    3. 检查 front-matter 中是否有 reviewed: true

    Args:
        vault_path: Vault 目录的绝对路径。

    Returns:
        [{"name": "笔记名", "path": "完整路径", "status": "状态",
          "user_edited": bool, "reviewed": bool}, ...]
    """
    vp = Path(vault_path)
    if not vp.exists():
        logger.info("Vault 目录不存在，跳过编辑检测: %s", vault_path)
        return []

    results: list[dict] = []
    for fp in sorted(vp.rglob("*.md")):
        # 跳过 _drafts/ 子目录
        if "_drafts" in fp.parts:
            continue
        # 跳过 MOC 索引文件
        if fp.stem.startswith(_MOC_PREFIX):
            continue

        try:
            raw = fp.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            logger.warning("读取文件失败 %s: %s", fp, e)
            continue

        fm = parse_front_matter(raw)
        status = fm.get("status", "")
        reviewed = fm.get("reviewed", False) is True

        # 判断是否被用户编辑
        user_edited = False
        created_time = _parse_created_time(fm)
        if created_time is not None:
            try:
                mtime = fp.stat().st_mtime
                mtime_dt = datetime.fromtimestamp(mtime)
                delta = (mtime_dt - created_time).total_seconds()
                if delta > _EDIT_BUFFER_SECONDS:
                    user_edited = True
                    logger.debug("检测到用户编辑: %s (delta=%.0fs)", fp.stem, delta)
            except OSError:
                pass
        elif not status:
            # 没有 front-matter 也没有 status → 可能是手工笔记，跳过
            continue

        # 即使没有 created 字段，reviewed: true 也单独算 review
        if reviewed:
            logger.debug("检测到已审核笔记: %s", fp.stem)

        results.append(
            {
                "name": fp.stem,
                "path": str(fp),
                "status": status,
                "user_edited": user_edited,
                "reviewed": reviewed,
            }
        )

    edited_count = sum(1 for r in results if r["user_edited"])
    reviewed_count = sum(1 for r in results if r["reviewed"])
    logger.info(
        "编辑检测完成: %d 篇笔记, %d 篇曾被编辑, %d 篇已审核",
        len(results),
        edited_count,
        reviewed_count,
    )
    return results


def extract_feedback_signals(vault_path: str, edited_notes: list[dict]) -> dict:
    """从用户编辑过的笔记中提取反馈信号。

    对于 user_edited=True 的笔记：
    1. 读取当前内容
    2. 提取所有 [[双链]]
    3. 统计出现频率 ≥2 的链接作为 preferred_links

    Args:
        vault_path: Vault 目录路径（用于读取笔记内容）。
        edited_notes: detect_user_edits() 的返回结果。

    Returns:
        {
            "preferred_links": list[str],
            "avoid_links": [],
            "edited_count": int,
            "reviewed_count": int,
        }
    """
    user_edited = [n for n in edited_notes if n["user_edited"]]
    reviewed_count = sum(1 for n in edited_notes if n["reviewed"])

    if not user_edited:
        logger.info("没有用户编辑过的笔记，跳过信号提取")
        return {
            "preferred_links": [],
            "avoid_links": [],
            "edited_count": 0,
            "reviewed_count": reviewed_count,
        }

    from src.services.refiner_service import parse_links

    link_counter: Counter[str] = Counter()
    for note in user_edited:
        try:
            content = Path(note["path"]).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            logger.warning("读取笔记失败 %s: %s", note["name"], e)
            continue
        links = parse_links(content)
        link_counter.update(links)

    # 至少出现 2 次的链接为偏好链接
    preferred_links = [link for link, cnt in link_counter.most_common() if cnt >= 2]

    logger.info(
        "反馈信号提取完成: %d 个偏好链接, %d 篇编辑笔记, %d 篇已审核",
        len(preferred_links),
        len(user_edited),
        reviewed_count,
    )
    return {
        "preferred_links": preferred_links,
        "avoid_links": [],
        "edited_count": len(user_edited),
        "reviewed_count": reviewed_count,
    }


def get_feedback_context(feedback_signals: dict) -> str:
    """将反馈信号转换为可注入 Agent prompt 的上下文片段。

    Args:
        feedback_signals: extract_feedback_signals() 的返回结果。

    Returns:
        格式化的 prompt 片段字符串。无信号时返回空字符串。
    """
    parts: list[str] = []

    preferred = feedback_signals.get("preferred_links", [])
    if preferred:
        links_str = ", ".join(f"[[{link}]]" for link in preferred)
        parts.append(f"用户偏好链接（基于编辑历史）: {links_str}")

    edited_count = feedback_signals.get("edited_count", 0)
    if edited_count > 0:
        parts.append(
            f"注意：有 {edited_count} 篇笔记曾被用户编辑，" "请优先保证术语准确性和链接相关性"
        )

    reviewed_count = feedback_signals.get("reviewed_count", 0)
    if reviewed_count > 0:
        parts.append(f"有 {reviewed_count} 篇笔记已被用户审核通过，" "请参考其风格和链接选择")

    if not parts:
        return ""

    return "\n".join(parts)
