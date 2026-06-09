"""
Chunk splitting and indexing for RAG-enhanced Agent.

Splits Obsidian notes into semantic chunks based on heading/paragraph/sentence
boundaries, then stores them in SQLite + numpy for fast cosine-similarity retrieval.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("vidbrain.chunk")

TARGET_MIN = 300
TARGET_MAX = 600
HARD_MIN = 150
HARD_MAX = 800
SOFT_MIN = 20


def chunk_note_content(content: str) -> list[dict]:
    """Split note text into semantic chunks.

    Boundary priority (highest first):
      1. ##  headings  (always split)
      2. ### headings  (split when section exceeds HARD_MAX)
      3. Blank lines / paragraph breaks (split when section exceeds TARGET_MAX)
      4. Sentence endings (fallback, never cut mid-sentence)

    Returns list of dicts: {"content": str, "token_count": int}
    """
    text = _strip_frontmatter(content)
    if not text.strip():
        return [{"content": content.strip() or "(empty)", "token_count": 1}]

    sections = _split_by_h2(text)
    sections = _split_long_by_h3(sections)
    sections = _split_long_by_paragraph(sections)
    sections = _split_long_by_sentence(sections)
    sections = _merge_tiny_chunks(sections)

    result: list[dict] = []
    for sec in sections:
        text = sec.strip()
        if not text:
            continue
        result.append({
            "content": text,
            "token_count": _estimate_tokens(text),
        })

    if not result:
        return [{"content": content.strip() or "(empty)", "token_count": 1}]

    return result


def _strip_frontmatter(content: str) -> str:
    """Remove YAML front-matter (--- ... ---) from content."""
    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            return content[end + 3:].strip()
    return content


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars/token for ASCII, ~1.5 chars/token for CJK."""
    cjk = sum(1 for c in text if "一" <= c <= "鿿"
              or "぀" <= c <= "ゟ"
              or "가" <= c <= "힯")
    ascii_chars = len(text) - cjk
    return max(1, ascii_chars // 4 + cjk * 3 // 2)


def _split_by_h2(text: str) -> list[str]:
    parts = re.split(r"\n(?=## )", text)
    return [p.strip() for p in parts if p.strip()]


def _split_long_by_h3(sections: list[str]) -> list[str]:
    result: list[str] = []
    for sec in sections:
        if len(sec) <= HARD_MAX:
            result.append(sec)
        else:
            subs = re.split(r"\n(?=### )", sec)
            result.extend(s.strip() for s in subs if s.strip())
    return result


def _split_long_by_paragraph(sections: list[str]) -> list[str]:
    result: list[str] = []
    for sec in sections:
        if len(sec) <= TARGET_MAX:
            result.append(sec)
        else:
            paras = re.split(r"\n\s*\n", sec)
            result.extend(p.strip() for p in paras if p.strip())
    return result


def _split_long_by_sentence(sections: list[str]) -> list[str]:
    result: list[str] = []
    for sec in sections:
        if len(sec) <= HARD_MAX:
            result.append(sec)
        else:
            pattern = r"(?<=[。！？.!?\n])(?=\S)"
            parts = re.split(pattern, sec)
            merged: list[str] = []
            buf = ""
            for p in parts:
                p = p.strip()
                if not p:
                    continue
                if buf and len(buf) + len(p) < HARD_MIN:
                    buf += "\n" + p
                elif buf and len(buf) < HARD_MIN:
                    buf += "\n" + p
                else:
                    if buf:
                        merged.append(buf)
                    buf = p
            if buf:
                if merged and len(buf) < HARD_MIN:
                    merged[-1] = merged[-1] + "\n" + buf
                else:
                    merged.append(buf)
            result.extend(merged)
    return result


def _merge_tiny_chunks(sections: list[str]) -> list[str]:
    """Merge small fragments back into neighbouring chunks.

    * Chunks starting with a heading marker (``#``, ``##``, ``###``) >= SOFT_MIN
      are kept as standalone chunks.
    * Truly tiny heading chunks (< SOFT_MIN) are merged into the previous chunk.
    * Non-heading fragments are always merged into the previous chunk.
    """
    if not sections:
        return sections
    result: list[str] = []
    for sec in sections:
        cur = sec.strip()
        if not cur:
            continue
        is_heading = re.match(r"^#{1,3}\s", cur)
        if result and (not is_heading or len(cur) < SOFT_MIN):
            result[-1] = result[-1] + "\n" + cur
        else:
            result.append(cur)
    return result
