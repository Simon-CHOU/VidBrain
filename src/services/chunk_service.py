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

TARGET_MAX = 600
HARD_MIN = 150
HARD_MAX = 800


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
    return max(1, ascii_chars // 4 + cjk * 2 // 3)


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

    * Chunks smaller than HARD_MIN (even heading chunks) are merged into the
      previous chunk.
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
        if result and (not is_heading or len(cur) < HARD_MIN):
            result[-1] = result[-1] + "\n" + cur
        else:
            result.append(cur)
    return result


# ── ChunkStore ─────────────────────────────────────────────────────────

@dataclass
class ChunkContext:
    """A retrieved chunk with its neighbor context."""
    chunk_id: str
    note_name: str
    content: str
    left_context: str | None
    right_context: str | None
    similarity: float

    def full_context(self) -> str:
        """Return left neighbor + matched chunk + right neighbor."""
        parts: list[str] = []
        if self.left_context:
            parts.append(self.left_context)
        parts.append(self.content)
        if self.right_context:
            parts.append(self.right_context)
        return "\n\n".join(parts)


class ChunkStore:
    """SQLite-backed chunk index with numpy vector storage.

    File layout in vault directory:
      .vidbrain_chunks.db          — SQLite metadata
      .vidbrain_chunk_vectors.npy  — float32 matrix, rows aligned with DB chunk order
    """

    DB_FILENAME = ".vidbrain_chunks.db"
    NPY_FILENAME = ".vidbrain_chunk_vectors.npy"
    VECTOR_DIM = 1024

    def __init__(self, vault_path: str) -> None:
        import sqlite3
        import numpy as np

        self._vault = Path(vault_path)
        self._db_path = self._vault / self.DB_FILENAME
        self._npy_path = self._vault / self.NPY_FILENAME
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._ensure_schema()

        if self._npy_path.exists():
            try:
                self._vectors = np.load(str(self._npy_path))
            except Exception:
                logger.warning("损坏的向量文件，重建空矩阵")
                self._vectors = np.empty((0, self.VECTOR_DIM), dtype=np.float32)
        else:
            self._vectors = np.empty((0, self.VECTOR_DIM), dtype=np.float32)
            self._save_vectors()

    def _ensure_schema(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS chunk_index (
                chunk_id    TEXT PRIMARY KEY,
                note_name   TEXT NOT NULL,
                note_mtime  REAL NOT NULL,
                chunk_index INTEGER NOT NULL,
                prev_chunk_id TEXT,
                next_chunk_id TEXT,
                content     TEXT NOT NULL,
                token_count INTEGER NOT NULL
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_note_name ON chunk_index(note_name)"
        )
        self._conn.commit()

    # ── public API ─────────────────────────────────────────────────

    def chunk_note(self, note_name: str, content: str, engine) -> None:
        """Chunk a note, embed all chunks, and write to index. Replaces existing chunks."""
        import numpy as np

        self.remove_note(note_name)
        chunks = chunk_note_content(content)
        if not chunks:
            return

        chunk_ids = [f"{note_name}#{i}" for i in range(len(chunks))]
        texts = [c["content"] for c in chunks]

        try:
            vectors = engine.embed_batch(texts)
        except Exception:
            logger.warning("批量 embedding 失败，回退到逐个 embed")
            vectors = []
            for t in texts:
                try:
                    vectors.append(engine.embed(t))
                except Exception as e:
                    logger.error("embed 失败: %s", str(e))
                    vectors.append([0.0] * self.VECTOR_DIM)

        vec_matrix = np.array(vectors, dtype=np.float32)

        note_mtime = 0.0
        note_path = self._vault / f"{note_name}.md"
        if note_path.exists():
            note_mtime = note_path.stat().st_mtime

        for i, c in enumerate(chunks):
            prev_id = chunk_ids[i - 1] if i > 0 else None
            next_id = chunk_ids[i + 1] if i < len(chunks) - 1 else None
            self._conn.execute(
                """INSERT INTO chunk_index
                   (chunk_id, note_name, note_mtime, chunk_index,
                    prev_chunk_id, next_chunk_id, content, token_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (chunk_ids[i], note_name, note_mtime, i,
                 prev_id, next_id, c["content"], c["token_count"]),
            )

        self._conn.commit()

        if len(self._vectors) == 0:
            self._vectors = vec_matrix
        else:
            self._vectors = np.vstack([self._vectors, vec_matrix])

        self._save_vectors()
        logger.info("ChunkStore: 已索引笔记 %s (%d chunks)", note_name, len(chunks))

    def remove_note(self, note_name: str) -> None:
        """Remove all chunks for a note. Vector matrix is rebuilt without them."""
        import numpy as np

        rows = self._conn.execute(
            "SELECT chunk_id FROM chunk_index WHERE note_name = ?",
            (note_name,),
        ).fetchall()

        if not rows:
            return

        remove_ids = {r["chunk_id"] for r in rows}
        all_rows = self._conn.execute(
            "SELECT chunk_id FROM chunk_index ORDER BY chunk_id"
        ).fetchall()
        keep_indices = [
            i for i, r in enumerate(all_rows)
            if r["chunk_id"] not in remove_ids
        ]

        self._conn.execute(
            "DELETE FROM chunk_index WHERE note_name = ?", (note_name,)
        )
        self._conn.commit()

        if keep_indices:
            self._vectors = self._vectors[keep_indices]
        else:
            self._vectors = np.empty((0, self.VECTOR_DIM), dtype=np.float32)

        self._save_vectors()

    def find_similar(
        self, query_vec: list[float], top_k: int = 5
    ) -> list[ChunkContext]:
        """Return top-k most similar chunks with neighbor context."""
        import numpy as np

        if len(self._vectors) == 0:
            return []

        query = np.array(query_vec, dtype=np.float32)
        query_norm = np.linalg.norm(query)
        vecs_norm = np.linalg.norm(self._vectors, axis=1)

        if query_norm == 0:
            return []

        denom = vecs_norm * query_norm
        denom[denom == 0] = 1e-10
        sims = np.dot(self._vectors, query) / denom

        n = len(sims)
        k = min(top_k, n)
        if k == 0:
            return []

        if k >= n:
            indices = np.argsort(sims)[::-1]
        else:
            indices = np.argpartition(sims, -k)[-k:]
            indices = indices[np.argsort(sims[indices])[::-1]]

        all_rows = self._conn.execute(
            "SELECT chunk_id FROM chunk_index ORDER BY chunk_id"
        ).fetchall()

        results: list[ChunkContext] = []
        for idx in indices:
            if sims[idx] < 0.3:
                continue
            row_idx = int(idx)
            if row_idx >= len(all_rows):
                continue
            chunk_id = all_rows[row_idx]["chunk_id"]
            row = self._conn.execute(
                "SELECT * FROM chunk_index WHERE chunk_id = ?", (chunk_id,)
            ).fetchone()
            if row is None:
                continue

            left = self._get_chunk_content(row["prev_chunk_id"])
            right = self._get_chunk_content(row["next_chunk_id"])

            results.append(ChunkContext(
                chunk_id=row["chunk_id"],
                note_name=row["note_name"],
                content=row["content"],
                left_context=left,
                right_context=right,
                similarity=float(sims[idx]),
            ))

        return results

    def is_stale(self, note_name: str, current_mtime: float) -> bool:
        row = self._conn.execute(
            "SELECT note_mtime FROM chunk_index WHERE note_name = ? LIMIT 1",
            (note_name,),
        ).fetchone()
        if row is None:
            return True
        return abs(row["note_mtime"] - current_mtime) > 0.01

    def get_all_note_names(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT note_name FROM chunk_index"
        ).fetchall()
        return [r["note_name"] for r in rows]

    def get_unchunked_notes(self, vault_path: str) -> list[tuple[str, float]]:
        """Scan vault for .md files not yet in the chunk index.

        Returns list of (note_name, mtime) for notes that need chunking.
        """
        vault = Path(vault_path)
        indexed = set(self.get_all_note_names())
        result: list[tuple[str, float]] = []
        for md_file in sorted(vault.glob("*.md")):
            stem = md_file.stem
            if stem.startswith(".") or stem.startswith("_"):
                continue
            if stem in indexed:
                if not self.is_stale(stem, md_file.stat().st_mtime):
                    continue
            result.append((stem, md_file.stat().st_mtime))
        return result

    # ── internal ───────────────────────────────────────────────────

    def _get_chunk_content(self, chunk_id: str | None) -> str | None:
        if chunk_id is None:
            return None
        row = self._conn.execute(
            "SELECT content FROM chunk_index WHERE chunk_id = ?",
            (chunk_id,),
        ).fetchone()
        return row["content"] if row else None

    def _save_vectors(self) -> None:
        import numpy as np
        np.save(str(self._npy_path), self._vectors)
