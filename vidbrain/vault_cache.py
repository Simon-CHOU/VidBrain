"""
Vault 笔记列表缓存。

避免每次处理视频时都全量扫描 Obsidian Vault 目录，
通过缓存笔记 stem 列表和质量评分，按需增量更新。
"""

from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path

logger = logging.getLogger("vidbrain.vault_cache")


def _read_note_quality_from_content(content: str) -> int:
    """从笔记 front-matter 读取质量评分。"""
    try:
        match = re.search(r"^quality_score:\s*(\d+)", content, re.MULTILINE)
        if match:
            return int(match.group(1))
    except Exception:
        pass
    return 0


def _strip_front_matter(content: str, max_len: int = 500) -> str:
    """去除 YAML front-matter，返回正文前 max_len 字符。"""
    if content.startswith("---"):
        parts = content.split("---", 2)
        body = parts[2] if len(parts) >= 3 else content
    else:
        body = content
    return body.strip()[:max_len]


def _read_preview_from_disk(vault_path: str, stem: str) -> str:
    """直接从磁盘读取笔记内容预览。"""
    file_path = Path(vault_path) / f"{stem}.md"
    try:
        if file_path.is_file():
            content = file_path.read_text(encoding="utf-8", errors="replace")
            return _strip_front_matter(content)
    except OSError:
        pass
    return ""


class VaultCache:
    """Vault 笔记列表缓存，线程安全。

    缓存 {stem: quality_score} 映射和内容预览（前 500 字符），
    通过扫描所有 .md 文件的 mtime 来检测变更，仅在检测到变更时重新扫描。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # 缓存数据
        self._vault_path: str = ""
        self._stems: dict[str, int] = {}  # stem -> quality_score
        self._content_previews: dict[str, str] = {}  # stem -> 内容预览 (去 front-matter 后前 500 字符)
        self._file_mtimes: dict[str, float] = {}  # file_path_str -> mtime
        self._last_full_scan: float = 0.0
        # 降频：至少间隔 N 秒才做全量变更检查
        self._scan_cooldown: float = 30.0

    def get_existing_notes(self, vault_path: str) -> list[str]:
        """获取按 quality_score 降序排列的笔记 stem 列表。

        首次调用时扫描整个 vault，后续调用仅在检测到文件变更时
        增量更新缓存。
        """
        vault = Path(vault_path)
        if not vault.exists():
            return []

        with self._lock:
            # 首次加载或 vault 路径变更 → 全量扫描
            if self._vault_path != vault_path or not self._stems:
                self._full_scan(vault)
                return self._sorted_stems()

            # 降频检查：冷却时间内直接返回缓存
            now = time.time()
            if now - self._last_full_scan < self._scan_cooldown:
                return self._sorted_stems()

            # 检查是否有文件变更
            if self._has_changes(vault):
                logger.info("[VaultCache] 检测到笔记变更，更新缓存")
                self._full_scan(vault)

            return self._sorted_stems()

    def invalidate(self) -> None:
        """强制下次访问时重新扫描。"""
        with self._lock:
            self._stems.clear()
            self._content_previews.clear()
            self._file_mtimes.clear()
            logger.info("[VaultCache] 缓存已失效")

    def get_content_preview(self, stem: str, max_chars: int = 400, vault_path: str = "") -> str:
        """获取笔记内容预览（去 front-matter），优先从缓存读取，缓存未命中时回退到磁盘读取。

        Args:
            stem: 笔记 stem
            max_chars: 最大返回字符数
            vault_path: 可选的 vault 路径，用于缓存未命中时回退到磁盘

        Returns:
            内容预览字符串，未缓存时返回空字符串
        """
        with self._lock:
            # 如果传入的 vault_path 与缓存的不同，则直接读磁盘（避免状态污染）
            if vault_path and vault_path != self._vault_path:
                body = _read_preview_from_disk(vault_path, stem)
                return body[:max_chars] if body else ""

            preview = self._content_previews.get(stem, "")
            if preview:
                return preview[:max_chars]
            # 缓存未命中，回退到磁盘读取
            vp = vault_path or self._vault_path
            if vp:
                body = _read_preview_from_disk(vp, stem)
                if body:
                    self._content_previews[stem] = body  # 缓存以供后续使用
                    return body[:max_chars]
            return ""

    def add_note(self, vault_path: str, stem: str, content: str = "") -> None:
        """增量添加单个笔记到缓存（用于新创建的笔记）。"""
        with self._lock:
            if vault_path != self._vault_path:
                return  # vault 已变更，下次全量扫描
            score = _read_note_quality_from_content(content) if content else 0
            self._stems[stem] = score
            self._content_previews[stem] = _strip_front_matter(content)
            file_path = str(Path(vault_path) / f"{stem}.md")
            try:
                self._file_mtimes[file_path] = Path(file_path).stat().st_mtime
            except OSError:
                pass

    # ── 内部方法 ──

    def _full_scan(self, vault: Path) -> None:
        """全量扫描 vault 目录，重建缓存。"""
        self._vault_path = str(vault)
        self._stems.clear()
        self._content_previews.clear()
        self._file_mtimes.clear()
        count = 0
        for p in vault.rglob("*.md"):
            if "_drafts" in p.parts:
                continue
            stem = p.stem
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
                score = _read_note_quality_from_content(content)
                preview = _strip_front_matter(content)
            except Exception:
                score = 0
                preview = ""
            self._stems[stem] = score
            self._content_previews[stem] = preview
            self._file_mtimes[str(p)] = p.stat().st_mtime
            count += 1
        self._last_full_scan = time.time()
        logger.info("[VaultCache] 全量扫描完成: %d 篇笔记", count)

    def _has_changes(self, vault: Path) -> bool:
        """检查 vault 是否有文件变更（新增/删除/修改）。"""
        current_files: set[str] = set()
        has_change = False

        for p in vault.rglob("*.md"):
            if "_drafts" in p.parts:
                continue
            path_str = str(p)
            current_files.add(path_str)

            cached_mtime = self._file_mtimes.get(path_str)
            if cached_mtime is None:
                # 新文件
                has_change = True
            else:
                try:
                    if p.stat().st_mtime > cached_mtime:
                        has_change = True
                except OSError:
                    has_change = True

            if has_change:
                break

        # 检查是否有文件被删除
        if not has_change:
            cached_files = set(self._file_mtimes.keys())
            deleted = cached_files - current_files
            if deleted:
                has_change = True

        return has_change

    def _sorted_stems(self) -> list[str]:
        """返回按 quality_score 降序排列的 stem 列表。"""
        return sorted(self._stems.keys(), key=lambda s: self._stems.get(s, 0), reverse=True)


# ── 全局单例 ──

_vault_cache: VaultCache | None = None
_cache_lock = threading.Lock()


def get_vault_cache() -> VaultCache:
    """获取 VaultCache 全局单例。"""
    global _vault_cache
    if _vault_cache is None:
        with _cache_lock:
            if _vault_cache is None:
                _vault_cache = VaultCache()
    return _vault_cache
