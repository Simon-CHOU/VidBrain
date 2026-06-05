"""
知识库精炼器。

功能：
1. 扫描 Vault 笔记，解析 [[双链]] 关系
2. 检测孤立笔记（无出链 / 无入链）
3. 批量调用 DeepSeek API 补充双链
4. 按主题生成 MOC（Map of Content）索引笔记

设计原则：
- 只读取和写入 vault_dir 目录下的 .md 文件
- 不修改项目目录内的任何文件
- 所有操作可重复执行，不会重复创建 MOC
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from openai import OpenAI

from vidbrain.config import LLMConfig

logger = logging.getLogger("vidbrain.refiner")

# ── 匹配 Obsidian [[双链]] ──
_LINK_PATTERN = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")

# ── MOC 笔记文件名前缀 ──
_MOC_PREFIX = "MOC-"


def parse_links(text: str) -> list[str]:
    """从 Markdown 文本中提取所有 [[双链]] 的目标笔记名。"""
    return [m.group(1).strip() for m in _LINK_PATTERN.finditer(text)]


def read_note(path: Path) -> dict[str, Any]:
    """读取单篇笔记，返回 {name, content, outgoing_links}。"""
    content = path.read_text(encoding="utf-8", errors="replace")
    name = path.stem
    links = parse_links(content)
    return {"name": name, "content": content, "outgoing_links": links, "path": str(path)}


def scan_vault(vault_path: str) -> list[dict[str, Any]]:
    """扫描 vault 目录，返回所有笔记（排除已存在的 MOC 文件）。"""
    vp = Path(vault_path)
    if not vp.exists():
        logger.warning("Vault 目录不存在: %s", vault_path)
        return []

    notes = []
    for fp in sorted(vp.rglob("*.md")):
        # 跳过已有的 MOC 文件
        if fp.stem.startswith(_MOC_PREFIX):
            continue
        notes.append(read_note(fp))

    logger.info("扫描完成: vault 中共 %d 篇笔记", len(notes))
    return notes


def analyze_links(notes: list[dict[str, Any]]) -> dict[str, Any]:
    """分析笔记间的双链关系。

    Returns:
        report: {
            "outgoing_counts": {note_name: count_of_outgoing_links},
            "incoming_counts": {note_name: count_of_incoming_links},
            "orphan_no_outgoing": [notes with 0 outgoing links],
            "orphan_no_incoming": [notes with 0 incoming links],
        }
    """
    outgoing = {n["name"]: len(n["outgoing_links"]) for n in notes}

    incoming: dict[str, int] = defaultdict(int)
    for n in notes:
        for link_name in n["outgoing_links"]:
            incoming[link_name] += 1

    orphan_out = [n for n in notes if outgoing.get(n["name"], 0) == 0]
    orphan_in = [n for n in notes if incoming.get(n["name"], 0) == 0]

    return {
        "outgoing_counts": outgoing,
        "incoming_counts": dict(incoming),
        "orphan_no_outgoing": orphan_out,
        "orphan_no_incoming": orphan_in,
    }


def _call_llm_batch(
    client: OpenAI, model: str, notes: list[dict[str, Any]], all_titles: list[str]
) -> list[dict[str, Any]]:
    """批量调用 LLM 为一组笔记生成双链建议。

    将多篇笔记打包到一次 API 调用中以节省 Token。
    """
    titles_str = "\n".join(f"- {t}" for t in sorted(all_titles))

    batch_input = ""
    for n in notes:
        preview = n["content"][:800]
        batch_input += f"\n### 笔记：{n['name']}\n```\n{preview}\n...\n```\n"

    prompt = (
        "你是一个 Obsidian 知识库架构师。以下是一个 vault 中所有笔记的标题列表，"
        "以及若干篇当前缺少 [[双向链接]] 的笔记内容预览。\n\n"
        f"## 所有笔记标题\n{titles_str}\n\n"
        "## 待补充链接的笔记\n"
        f"{batch_input}\n\n"
        "请对每篇笔记，分析其内容与哪些标题存在关联，输出 JSON 格式：\n"
        "```json\n"
        '{"suggestions": [\n'
        '  {"note": "笔记名", "links": ["关联笔记1", "关联笔记2", ...]}\n'
        "]}\n```\n"
        "要求：\n"
        "1. 只从「所有笔记标题」中选择关联项\n"
        "2. 每篇笔记推荐 2-5 个链接\n"
        "3. 如果笔记内容为空或无可关联项，返回空数组"
    )

    for attempt in range(1, 4):
        try:
            kwargs = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "timeout": 60,
            }
            # DeepSeek 支持 response_format，OpenAI 兼容接口也支持
            response = client.chat.completions.create(**kwargs)
            raw = response.choices[0].message.content or "{}"
            # 尝试从响应中提取 JSON（LLM 可能在 markdown 代码块中返回）
            json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
            if json_match:
                raw = json_match.group(1)
            result = json.loads(raw)
            suggestions = result.get("suggestions", [])
            logger.info(
                "LLM 批量建议完成: %d 篇笔记获得链接建议",
                len(suggestions),
            )
            return suggestions
        except Exception as e:
            logger.warning("LLM 批量建议失败 (尝试 %d/3): %s", attempt, str(e))
            if attempt < 3:
                time.sleep(2 ** (attempt - 1))
            else:
                return []


def apply_suggestions(
    vault_path: str, suggestions: list[dict[str, Any]], notes: list[dict[str, Any]]
) -> int:
    """将 LLM 建议的双链写入笔记末尾。"""
    note_map = {n["name"]: n for n in notes}
    applied = 0

    for item in suggestions:
        name = item.get("note", "")
        links = item.get("links", [])
        if not links or name not in note_map:
            continue

        note = note_map[name]
        # 过滤已存在的链接
        existing = set(note["outgoing_links"])
        new_links = [l for l in links if l not in existing]
        if not new_links:
            continue

        # 在笔记末尾追加双链
        link_block = "\n\n---\n*[自动补充双向链接]*\n" + " ".join(
            f"[[{l}]]" for l in new_links
        )
        fp = Path(note["path"])
        fp.write_text(note["content"] + link_block, encoding="utf-8")
        applied += 1
        logger.info("  已补充: %s → %s", name, ", ".join(new_links))

    return applied


def _extract_topics(
    client: OpenAI, model: str, notes: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """分析所有笔记标题，提取主题分类。"""
    titles = [n["name"] for n in notes]
    titles_str = "\n".join(f"- {t}" for t in titles)

    prompt = (
        "以下是一个 Obsidian 知识库中所有笔记的标题列表。请根据标题推测主题分类，"
        "将笔记分组到 3-8 个主题下。输出 JSON 格式：\n"
        "```json\n"
        '{"topics": [\n'
        '  {"topic": "主题名", "notes": ["笔记1", "笔记2", ...], "description": "主题简述"}\n'
        "]}\n```\n"
        f"## 笔记标题\n{titles_str}"
    )

    for attempt in range(1, 4):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                timeout=60,
            )
            raw = response.choices[0].message.content or "{}"
            # 尝试从响应中提取 JSON（LLM 可能在 markdown 代码块中返回）
            json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
            if json_match:
                raw = json_match.group(1)
            else:
                # 尝试提取第一个 { 到最后一个 } 之间的内容
                start = raw.find("{")
                end = raw.rfind("}")
                if start >= 0 and end > start:
                    raw = raw[start : end + 1]
            result = json.loads(raw)
            topics = result.get("topics", [])
            logger.info("主题提取完成: %d 个主题", len(topics))
            return topics
        except Exception as e:
            logger.warning("主题提取失败 (尝试 %d/3): %s", attempt, str(e))
            if attempt < 3:
                time.sleep(2 ** (attempt - 1))
            else:
                return []


def generate_moc_files(vault_path: str, topics: list[dict[str, Any]]) -> int:
    """为主题生成 MOC 索引笔记文件。"""
    vp = Path(vault_path)
    created = 0

    for topic in topics:
        topic_name = topic.get("topic", "")
        notes_in_topic = topic.get("notes", [])
        description = topic.get("description", "")
        if not topic_name or not notes_in_topic:
            continue

        safe_name = re.sub(r'[\\/:*?"<>|]', "-", topic_name)
        filename = f"{_MOC_PREFIX}{safe_name}.md"
        filepath = vp / filename

        # 如果文件已存在，跳过
        if filepath.exists():
            logger.info("  MOC 已存在，跳过: %s", filename)
            continue

        lines = [
            f"# {topic_name}\n",
            f"\n{description}\n" if description else "",
            "\n## 相关笔记\n",
        ]
        for note_name in notes_in_topic:
            lines.append(f"- [[{note_name}]]\n")

        filepath.write_text("".join(lines), encoding="utf-8")
        created += 1
        logger.info("  创建 MOC: %s (%d 篇笔记)", filename, len(notes_in_topic))

    return created


def refine_vault(vault_path: str, llm_config: LLMConfig) -> None:
    """知识库精炼主入口。

    执行流程：
    1. 扫描 vault 中的所有笔记
    2. 分析双链关系，找出孤立笔记
    3. 批量补充孤立笔记的双链
    4. 生成 MOC 主题索引
    """
    logger.info("=" * 50)
    logger.info("知识库精炼开始")
    logger.info("Vault: %s", vault_path)
    logger.info("=" * 50)

    # Step 1: 扫描
    notes = scan_vault(vault_path)
    if not notes:
        logger.info("没有笔记需要处理")
        return

    # Step 2: 分析
    report = analyze_links(notes)
    orphan_out = report["orphan_no_outgoing"]
    orphan_in = report["orphan_no_incoming"]
    logger.info(
        "链接分析: 无出链=%d 篇, 无入链=%d 篇",
        len(orphan_out), len(orphan_in),
    )

    # Step 3: 批量补充双链（优先处理无出链的笔记）
    if orphan_out:
        logger.info("阶段 1/2: 批量补充双向链接...")
        client = OpenAI(api_key=llm_config.api_key, base_url=llm_config.base_url)
        all_titles = [n["name"] for n in notes]

        # 分批处理，每批最多 10 篇
        batch_size = 10
        total_applied = 0
        for i in range(0, len(orphan_out), batch_size):
            batch = orphan_out[i : i + batch_size]
            logger.info("  处理第 %d-%d 篇...", i + 1, min(i + batch_size, len(orphan_out)))
            suggestions = _call_llm_batch(client, llm_config.model, batch, all_titles)
            if suggestions:
                applied = apply_suggestions(vault_path, suggestions, notes)
                total_applied += applied

        logger.info("双向链接补充完成: 共修改 %d 篇笔记", total_applied)
    else:
        logger.info("没有需要补充链接的笔记")

    # Step 4: 生成 MOC
    logger.info("阶段 2/2: 生成 MOC 主题索引...")
    client = OpenAI(api_key=llm_config.api_key, base_url=llm_config.base_url)
    topics = _extract_topics(client, llm_config.model, notes)
    if topics:
        created = generate_moc_files(vault_path, topics)
        logger.info("MOC 生成完成: 新建 %d 个索引文件", created)
    else:
        logger.info("未生成 MOC 文件")

    logger.info("=" * 50)
    logger.info("知识库精炼完成")
    logger.info("=" * 50)
