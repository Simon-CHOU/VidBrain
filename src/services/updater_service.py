"""
增量内容更新模块。

当新笔记写入 Vault 后，自动检测是否需要对已有笔记进行补充更新。
流程：提取新笔记关键词 → 匹配关联笔记 → LLM 建议更新 → 应用更新。
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

from openai import OpenAI

from src.models.config import LLMConfig
from src.utils.vault_cache import get_vault_cache

logger = logging.getLogger("vidbrain.updater")


def _check_related_embedding(
    vault_path: str,
    new_content: str,
    embedding_store,
    embedding_engine,
) -> list[dict]:
    """使用 embedding 检索关联笔记。"""
    if not new_content or len(new_content) < 50:
        return []
    if not embedding_store.all_stems():
        return []

    # 嵌入新笔记内容（前 1000 字符）
    query_vec = embedding_engine.embed(new_content[:1000])
    # 检索 top-5 最相似笔记
    similar = embedding_store.find_similar(query_vec, top_k=5)
    # 过滤相似度 < 0.5
    similar = [(s, sim) for s, sim in similar if sim >= 0.5]

    if not similar:
        return []

    vault_cache = get_vault_cache()
    results: list[dict] = []
    for stem, sim in similar[:3]:
        # 从缓存获取内容预览，避免磁盘读取
        content_preview = vault_cache.get_content_preview(stem, vault_path=vault_path)
        results.append(
            {
                "name": stem,
                "stem": stem,
                "match_terms": [],
                "score": round(sim, 3),
                "content_preview": content_preview,
            }
        )

    logger.info(
        "[Updater Embedding] 检索到 %d 篇关联笔记: %s",
        len(results),
        [r["stem"] for r in results],
    )
    return results


def _extract_key_terms(content: str) -> list[str]:
    """从内容中提取关键术语。

    启发式规则：
    1. 反引号包裹的术语，如 `CUDA`、`FlashAttention`
    2. ## 标题中长度 > 4 的单词（中文字符也计数）
    """
    terms: list[str] = []
    seen: set[str] = set()

    # 截取前 200 字符用于提取
    snippet = content[:200]

    # 1. 反引号包裹的术语: `like_this`
    for m in re.finditer(r"`([^`]+)`", snippet):
        term = m.group(1).strip()
        if len(term) >= 2 and term not in seen:
            terms.append(term)
            seen.add(term)

    # 2. ## 标题中的单词（长度 > 4 的单词）
    for m in re.finditer(r"^#{1,6}\s+(.+)$", snippet, re.MULTILINE):
        heading_text = m.group(1).strip()
        # 分割出单词（英文单词或中文连续字符）
        words = re.findall(r"[\w\u4e00-\u9fff]+", heading_text)
        for w in words:
            if len(w) > 4 and w.lower() not in seen:
                terms.append(w)
                seen.add(w.lower())

    return terms


def _match_notes(
    terms: list[str], existing_notes: list[str], top_n: int = 3
) -> list[dict]:
    """将术语与已有笔记 stem 进行匹配，返回 top N 匹配结果。"""
    results: list[dict] = []
    for note_stem in existing_notes:
        matched: list[str] = []
        stem_lower = note_stem.lower()
        for term in terms:
            term_lower = term.lower()
            # 用简单的子串匹配（因为 stem 和 term 都是单概念）
            if term_lower in stem_lower or stem_lower in term_lower:
                matched.append(term)
        if matched:
            results.append(
                {
                    "name": note_stem,
                    "stem": note_stem,
                    "match_terms": matched,
                    "score": len(matched),
                }
            )

    # 按匹配数排序，取 top N
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:top_n]


def check_related_notes(
    vault_path: str,
    new_note_name: str,
    new_content: str,
    existing_notes: list[str],
    embedding_enabled: bool = False,
    embedding_store=None,
    embedding_engine=None,
) -> list[dict]:
    """检测新笔记与已有笔记的关联。

    Args:
        vault_path: Vault 根目录路径
        new_note_name: 新笔记名
        new_content: 新笔记内容
        existing_notes: 已有笔记 stem 列表
        embedding_enabled: 是否启用 embedding 检索
        embedding_store: EmbeddingStore 实例
        embedding_engine: EmbeddingEngine 实例

    Returns:
        关联笔记列表，每项包含 name, stem, match_terms, content_preview
    """
    # embedding 路径
    if (
        embedding_enabled
        and embedding_store is not None
        and embedding_engine is not None
    ):
        return _check_related_embedding(
            vault_path,
            new_content,
            embedding_store,
            embedding_engine,
        )

    # 原始子串匹配路径
    terms = _extract_key_terms(new_content)
    logger.debug("提取到 %d 个关键术语: %s", len(terms), terms)

    if not terms:
        return []

    related = _match_notes(terms, existing_notes)
    if not related:
        return []

    # 从缓存获取内容预览（避免磁盘读取，缓存未命中时回退到磁盘）
    vault_cache = get_vault_cache()
    for r in related:
        r["content_preview"] = vault_cache.get_content_preview(
            r["stem"], vault_path=vault_path
        )

    logger.info(
        "检测到 %d 篇关联笔记 (top %d): %s",
        len(related),
        min(3, len(related)),
        [r["stem"] for r in related],
    )
    return related


def suggest_update(
    new_note_name: str,
    new_content: str,
    related_notes: list[dict],
    client: OpenAI,
    model: str,
) -> list[dict]:
    """调用 LLM 决定是否对关联笔记进行更新。

    Args:
        new_note_name: 新笔记名
        new_content: 新笔记内容
        related_notes: check_related_notes 返回的关联笔记列表
        client: OpenAI 客户端
        model: 模型名称

    Returns:
        建议列表，每项包含 target_note, type, content
    """
    if not related_notes:
        return []

    # 构建关联笔记摘要
    related_summary_parts: list[str] = []
    for rn in related_notes:
        preview = rn.get("content_preview", "")
        related_summary_parts.append(
            f"- 笔记: {rn['name']}\n  匹配术语: {', '.join(rn['match_terms'])}\n  内容预览: {preview[:200]}"
        )
    related_summary = "\n".join(related_summary_parts)

    new_preview = new_content[:600]

    prompt = (
        f"给定新笔记「{new_note_name}」和以下已有关联笔记，判断每篇已有笔记是否需要更新。\n\n"
        "选项说明：\n"
        "- 'none': 无需更新\n"
        "- 'ref': 在笔记底部添加参考链接\n"
        "- 'supplement': 添加补充内容\n\n"
        f"## 新笔记内容（预览）\n{new_preview}\n\n"
        f"## 已有关联笔记\n{related_summary}\n\n"
        "输出 JSON 格式：\n"
        '{"suggestions": [{"target_note": "<笔记名>", "type": "ref|supplement|none", '
        '"content": "要追加的 markdown 内容"}]}'
    )

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                timeout=60,
            )
            raw = response.choices[0].message.content or "{}"
            # 尝试从 markdown 代码块中提取 JSON
            json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
            if json_match:
                raw = json_match.group(1)
            result = json.loads(raw)
            suggestions = result.get("suggestions", [])

            # 过滤掉 "none" 类型，最多保留 3 条
            filtered = [s for s in suggestions if s.get("type", "none") != "none"]
            filtered = filtered[:3]

            logger.info(
                "LLM 更新建议: %d 条 (原始 %d 条)", len(filtered), len(suggestions)
            )
            return filtered
        except Exception as e:
            logger.warning(
                "LLM 更新建议失败 (尝试 %d/%d): %s", attempt, max_retries, str(e)
            )
            if attempt < max_retries:
                time.sleep(2 ** (attempt - 1))
    return []


def apply_update(vault_path: str, suggestion: dict) -> bool:
    """将更新应用到目标笔记。

    Args:
        vault_path: Vault 根目录路径
        suggestion: 单条建议，含 target_note, type, content

    Returns:
        是否成功应用
    """
    target_name = suggestion.get("target_note", "")
    new_note_name = suggestion.get("new_note_name", target_name)
    append_content = suggestion.get("content", "")

    if not target_name or not append_content:
        logger.warning("apply_update: 无效的建议内容")
        return False

    note_file = Path(vault_path) / f"{target_name}.md"
    if not note_file.exists():
        logger.warning("apply_update: 目标笔记不存在: %s", note_file)
        return False

    try:
        existing = note_file.read_text(encoding="utf-8", errors="replace")
        update_block = (
            f"\n\n---\n*[自动更新: 关联笔记 [[{new_note_name}]]]*\n{append_content}\n"
        )
        note_file.write_text(existing + update_block, encoding="utf-8")
        logger.info("已更新笔记: %s <- 关联自 [[%s]]", target_name, new_note_name)
        return True
    except OSError as e:
        logger.error("apply_update 写入失败: %s", str(e))
        return False


def check_and_update(
    vault_path: str,
    new_note_name: str,
    new_content: str,
    existing_notes: list[str],
    llm_config: LLMConfig,
) -> int:
    """编排器：检测关联笔记 → LLM 生成建议 → 应用更新。

    Args:
        vault_path: Vault 根目录路径
        new_note_name: 新笔记名
        new_content: 新笔记内容
        existing_notes: 已有笔记 stem 列表
        llm_config: LLM 配置

    Returns:
        被更新的笔记数量
    """
    # Step 1: 检测关联笔记
    related = check_related_notes(
        vault_path, new_note_name, new_content, existing_notes
    )
    if not related:
        logger.info("[Updater] 未检测到关联笔记，跳过更新")
        return 0

    # Step 2: LLM 生成更新建议
    client = OpenAI(api_key=llm_config.api_key, base_url=llm_config.base_url)
    suggestions = suggest_update(
        new_note_name, new_content, related, client, llm_config.model
    )
    if not suggestions:
        logger.info("[Updater] LLM 未生成更新建议")
        return 0

    # Step 3: 应用更新
    updated_count = 0
    for suggestion in suggestions:
        suggestion["new_note_name"] = new_note_name
        if apply_update(vault_path, suggestion):
            updated_count += 1

    logger.info("[Updater] 完成: 更新了 %d 篇笔记", updated_count)
    return updated_count
