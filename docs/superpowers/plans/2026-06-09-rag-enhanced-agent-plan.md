# RAG Enhanced Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Inject Embedding-based RAG retrieval into the three Agent nodes so LLM prompt context includes semantically relevant chunks from the vault, creating a self-improving feedback loop.

**Architecture:** Three-layer design — chunk splitting (by `##`/`###` headings + paragraph + sentence boundaries), chunk indexing (SQLite metadata + numpy vectors), and Agent prompt injection (top-k chunk context injected into each node's system prompt). Startup progressive indexing processes 20 uncached notes per batch.

**Tech Stack:** Python 3.10+, SQLite (existing), numpy (existing), openai (existing), pytest (existing)

---

## File Structure Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/services/chunk_service.py` | **Create** | `chunk_note_content()` + `ChunkStore` class |
| `tests/test_chunk_service.py` | **Create** | Unit tests for splitting + store |
| `src/models/state.py` | **Modify** | Add `rag_context` field to `AgentState` |
| `src/services/agent_service.py` | **Modify** | Add `rag_context` injection to 3 node prompts |
| `src/services/pipeline_service.py` | **Modify** | RAG retrieval before Agent, chunk indexing after write |
| `src/main.py` | **Modify** | Startup progressive indexing |
| `src/cli.py` | **Modify** | `--chunk-all` flag |
| `src/models/config.py` | **Modify** | `chunk_all: bool` in `PipelineConfig` |
| `tests/conftest.py` | **Modify** | Add `ChunkStore` test fixtures |

---

### Task 1: Chunk Splitting Function

**Files:**
- Create: `src/services/chunk_service.py`
- Create: `tests/test_chunk_service.py`

- [ ] **Step 1: Write the test file for chunk splitting**

Create `tests/test_chunk_service.py`:

```python
"""Tests for chunk_service — chunk splitting and ChunkStore."""
from __future__ import annotations

import pytest
from src.services.chunk_service import chunk_note_content


class TestChunkNoteContent:
    """Tests for chunk_note_content()."""

    def test_splits_by_h2_headings(self):
        content = "## Intro\nThis is intro content.\n\n## Main\nThis is main content."
        chunks = chunk_note_content(content)
        assert len(chunks) == 2
        assert chunks[0]["content"].startswith("## Intro")
        assert chunks[1]["content"].startswith("## Main")

    def test_splits_by_h3_in_long_sections(self):
        intro = "intro paragraph. " * 50
        content = f"## Overview\n{intro}\n\n### Detail A\nShort detail.\n\n### Detail B\nMore detail."
        chunks = chunk_note_content(content)
        assert len(chunks) >= 3

    def test_merges_tiny_chunks(self):
        content = "## A\nab\n\n## B\ncd\n\n## C\nA proper paragraph with enough characters to stand alone as a meaningful chunk."
        chunks = chunk_note_content(content)
        assert len(chunks) < 3

    def test_short_note_stays_single_chunk(self):
        content = "This is a very short note without headings."
        chunks = chunk_note_content(content)
        assert len(chunks) == 1
        assert "This is a very short note" in chunks[0]["content"]

    def test_strips_frontmatter(self):
        content = (
            "---\ntype: note\nstatus: auto\n---\n\n"
            "## Actual Content\nThis is the real content."
        )
        chunks = chunk_note_content(content)
        assert "---" not in chunks[0]["content"]
        assert "type:" not in chunks[0]["content"]

    def test_content_preserved_in_order(self):
        content = "## First\nFirst content.\n\n## Second\nSecond content.\n\n## Third\nThird content."
        chunks = chunk_note_content(content)
        assert len(chunks) == 3
        full = "".join(c["content"] for c in chunks)
        assert "First content" in full
        assert "Third content" in full

    def test_token_count_is_estimated(self):
        content = "## Test\nThis is test content with about thirty characters."
        chunks = chunk_note_content(content)
        assert "token_count" in chunks[0]
        assert isinstance(chunks[0]["token_count"], int)
        assert chunks[0]["token_count"] > 0
```

- [ ] **Step 2: Run tests to verify they fail**

```
python -m pytest tests/test_chunk_service.py -v
```

Expected: ModuleNotFoundError for `src.services.chunk_service`.

- [ ] **Step 3: Write the chunk splitting function**

Create `src/services/chunk_service.py`:

```python
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
    pattern = r"(?=^## )"
    parts = re.split(pattern, text, flags=re.MULTILINE)
    return [p.strip() for p in parts if p.strip()]


def _split_long_by_h3(sections: list[str]) -> list[str]:
    result: list[str] = []
    for sec in sections:
        if len(sec) <= HARD_MAX:
            result.append(sec)
        else:
            pattern = r"(?=^### )"
            subs = re.split(pattern, sec, flags=re.MULTILINE)
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
    if not sections:
        return sections
    result: list[str] = []
    i = 0
    while i < len(sections):
        cur = sections[i].strip()
        if len(cur) >= HARD_MIN:
            result.append(cur)
            i += 1
        elif i == 0 and len(sections) > 1:
            result.append(cur + "\n" + sections[i + 1].strip())
            i += 2
        elif i == len(sections) - 1:
            if result:
                result[-1] = result[-1] + "\n" + cur
            else:
                result.append(cur)
            i += 1
        else:
            if result:
                result[-1] = result[-1] + "\n" + cur
            else:
                result.append(cur)
            i += 1
    return result
```

- [ ] **Step 4: Run chunk splitting tests**

```
python -m pytest tests/test_chunk_service.py::TestChunkNoteContent -v
```

Expected: All 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/services/chunk_service.py tests/test_chunk_service.py
git commit -m "feat: add chunk_note_content() - semantic note splitting by headings/paragraphs/sentences"
```

---

### Task 2: ChunkStore (SQLite + numpy)

**Files:**
- Modify: `src/services/chunk_service.py` (append ChunkStore class)
- Modify: `tests/test_chunk_service.py` (append ChunkStore tests)

- [ ] **Step 1: Write ChunkStore tests**

Append to `tests/test_chunk_service.py`:

```python
import numpy as np
from unittest.mock import MagicMock

from src.services.chunk_service import ChunkStore


class TestChunkStore:
    """Tests for ChunkStore."""

    @pytest.fixture
    def mock_engine(self):
        engine = MagicMock()
        engine.embed_batch.return_value = [[0.1] * 1024]
        engine.embed.return_value = [0.1] * 1024
        return engine

    def make_store(self, vault_path: str) -> ChunkStore:
        return ChunkStore(vault_path)

    def test_init_creates_db_and_npy(self, tmp_path):
        store = self.make_store(str(tmp_path))
        assert (tmp_path / ".vidbrain_chunks.db").exists()
        assert (tmp_path / ".vidbrain_chunk_vectors.npy").exists()
        vecs = np.load(str(tmp_path / ".vidbrain_chunk_vectors.npy"))
        assert vecs.shape == (0, 1024)

    def test_chunk_note_stores_metadata(self, tmp_path, mock_engine):
        store = self.make_store(str(tmp_path))
        content = "## Intro\nThis is content about machine learning.\n\n## Details\nDeep dive into attention mechanisms."
        store.chunk_note("TestNote", content, mock_engine)
        assert "TestNote" in store.get_all_note_names()

    def test_find_similar_returns_chunks(self, tmp_path, mock_engine):
        store = self.make_store(str(tmp_path))
        content = "## Section A\nThe quick brown fox jumps over the lazy dog.\n\n## Section B\nMachine learning is transforming technology."
        store.chunk_note("NoteA", content, mock_engine)
        results = store.find_similar([0.1] * 1024, top_k=3)
        assert len(results) >= 1
        for r in results:
            assert hasattr(r, "content")
            assert hasattr(r, "similarity")
            assert hasattr(r, "note_name")

    def test_find_similar_includes_neighbor_context(self, tmp_path, mock_engine):
        store = self.make_store(str(tmp_path))
        content = "## One\nFirst chunk with enough text to meet minimum size requirements here.\n\n## Two\nSecond chunk also with enough text to meet the minimum size threshold for testing.\n\n## Three\nThird chunk that has enough content to be a proper chunk with minimum length satisfied."
        store.chunk_note("NeighborNote", content, mock_engine)
        results = store.find_similar([0.1] * 1024, top_k=3)
        if len(results) > 1:
            ctx = results[1].full_context()
            assert results[1].content in ctx

    def test_is_stale_detects_mtime_change(self, tmp_path, mock_engine):
        store = self.make_store(str(tmp_path))
        content = "## Test\nEnough content here to make a valid chunk with proper sizing."
        store.chunk_note("StaleTest", content, mock_engine)
        assert not store.is_stale("StaleTest", 100.0)
        assert store.is_stale("StaleTest", 200.0)

    def test_remove_note_cleans_up(self, tmp_path, mock_engine):
        store = self.make_store(str(tmp_path))
        content = "## A\nFirst chunk with sufficient content here.\n\n## B\nSecond chunk also with enough text to be meaningful."
        store.chunk_note("RemoveTest", content, mock_engine)
        assert "RemoveTest" in store.get_all_note_names()
        store.remove_note("RemoveTest")
        assert "RemoveTest" not in store.get_all_note_names()

    def test_get_unchunked_notes_finds_new_notes(self, tmp_path, mock_engine):
        store = self.make_store(str(tmp_path))
        (tmp_path / "Unchunked.md").write_text(
            "## Content\nSome content here with enough text for a valid chunk.",
            encoding="utf-8",
        )
        (tmp_path / "AlsoUnchunked.md").write_text("Minimal note.", encoding="utf-8")
        unchunked = store.get_unchunked_notes(str(tmp_path))
        assert len(unchunked) >= 2
        names = [n for n, _ in unchunked]
        assert "Unchunked" in names

    def test_get_unchunked_notes_excludes_indexed(self, tmp_path, mock_engine):
        store = self.make_store(str(tmp_path))
        note_path = tmp_path / "Indexed.md"
        note_path.write_text(
            "## Content\nSome content here with enough text for a valid chunk test.",
            encoding="utf-8",
        )
        store.chunk_note("Indexed", note_path.read_text(encoding="utf-8"), mock_engine)
        (tmp_path / "NotIndexed.md").write_text(
            "## Content\nDifferent content with sufficient length to be meaningful.",
            encoding="utf-8",
        )
        unchunked = store.get_unchunked_notes(str(tmp_path))
        names = [n for n, _ in unchunked]
        assert "Indexed" not in names
        assert "NotIndexed" in names

    def test_chunk_id_sequence_in_note(self, tmp_path, mock_engine):
        store = self.make_store(str(tmp_path))
        content = "## First\nFirst chunk with enough content to be a proper chunk for testing purposes.\n\n## Second\nSecond chunk also with sufficient content for the test requirements.\n\n## Third\nThird chunk with more content that meets minimum length.\n\n## Fourth\nFourth chunk with another piece of content for the test."
        store.chunk_note("SeqTest", content, mock_engine)
        results = store.find_similar([0.1] * 1024, top_k=10)
        note_chunks = [r for r in results if r.note_name == "SeqTest"]
        assert len(note_chunks) >= 2
        for c in note_chunks:
            assert c.chunk_id.startswith("SeqTest#")
```

- [ ] **Step 2: Run ChunkStore tests to verify they fail**

```
python -m pytest tests/test_chunk_service.py::TestChunkStore -v
```

Expected: ImportError or AttributeError for `ChunkStore`.

- [ ] **Step 3: Write the ChunkStore class**

Append to `src/services/chunk_service.py`:

```python
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
```

- [ ] **Step 4: Run ChunkStore tests**

```
python -m pytest tests/test_chunk_service.py::TestChunkStore -v
```

Expected: All 9 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/services/chunk_service.py tests/test_chunk_service.py
git commit -m "feat: add ChunkStore - SQLite + numpy chunk indexing with similarity search"
```

---

### Task 3: Startup Progressive Indexing

**Files:**
- Modify: `src/main.py`

- [ ] **Step 1: Add the progressive indexing function**

After the `_init_embedding_engine()` function in `src/main.py` (after line 109), insert:

```python
def _progressive_chunk_indexing(
    vault_path: str,
    chunk_store,
    emb_engine,
    batch_size: int = 20,
) -> int:
    """Process up to batch_size unindexed notes from vault.

    Returns number of notes chunked this batch.
    """
    unchunked = chunk_store.get_unchunked_notes(vault_path)
    if not unchunked:
        logger.info("Chunk 索引: vault 已全部索引完毕")
        return 0

    batch = unchunked[:batch_size]
    logger.info(
        "Chunk 索引: 发现 %d 篇未索引笔记，本批处理 %d 篇",
        len(unchunked), len(batch),
    )

    indexed = 0
    for note_name, _mtime in batch:
        note_path = Path(vault_path) / f"{note_name}.md"
        try:
            content = note_path.read_text(encoding="utf-8", errors="replace")
            chunk_store.chunk_note(note_name, content, emb_engine)
            indexed += 1
        except Exception as e:
            logger.warning("Chunk 索引失败 (%s): %s", note_name, str(e))

    logger.info("Chunk 索引: 本批完成 %d/%d 篇", indexed, len(batch))
    return indexed
```

- [ ] **Step 2: Wire progressive indexing into main() startup**

In `src/main.py`, find the embedding init block (lines 575-579):

```python
    emb_config = None
    emb_store = None
    if cfg.embedding_enabled:
        emb_config = _init_embedding()
        emb_store = _init_embedding_store(cfg.vault_dir)
```

Replace with:

```python
    emb_config = None
    emb_store = None
    if cfg.embedding_enabled:
        emb_config = _init_embedding()
        emb_store = _init_embedding_store(cfg.vault_dir)

        # Progressive chunk indexing for RAG-enhanced Agent
        from src.services.chunk_service import ChunkStore

        chunk_store = ChunkStore(cfg.vault_dir)
        emb_engine = _init_embedding_engine(emb_config)
        batch = 999999 if cfg.chunk_all else 20
        _progressive_chunk_indexing(cfg.vault_dir, chunk_store, emb_engine, batch_size=batch)
```

EmbeddingEngine is stateless (only wraps the API client), so creating one here for indexing while `process_pipeline` creates another on demand is fine.

- [ ] **Step 3: Commit**

```bash
git add src/main.py
git commit -m "feat: add startup progressive chunk indexing (20 notes/batch)"
```

---

### Task 4: Agent RAG Context Injection

**Files:**
- Modify: `src/models/state.py`
- Modify: `src/services/agent_service.py`

- [ ] **Step 1: Add rag_context to AgentState**

In `src/models/state.py`, add one field to the TypedDict (before `video_id` line, after `feedback_context`):

```python
    feedback_context: str
    rag_context: str
```

- [ ] **Step 2: Modify clean_and_extract_node to use rag_context**

In `src/services/agent_service.py`, replace `clean_and_extract_node` (lines 67-78):

```python
    def clean_and_extract_node(state: AgentState) -> Dict[str, Any]:
        """节点 1：术语纠错 + 分段 + 提炼核心知识。"""
        logger.info("[Agent] 清洗与提炼: %s", state["video_name"])
        rag = state.get("rag_context", "")
        if rag:
            prompt = (
                "你是一个资深的 AI Infrastructure 技术文档专家。\n\n"
                "[系统参考]\n"
                "知识库中已存在以下相关内容片段：\n"
                f"{rag}\n\n"
                "[任务]\n"
                "请对以下技术视频的原始 ASR 文本进行处理。"
                "参考上方片段中的术语写法——如果参考片段中的术语比 ASR 文本更准确，优先采用参考片段的写法。\n"
                "1. 修正错别字，尤其是专业技术术语（例如将'扣打'修正为'CUDA'，'卡夫卡'修正为'Kafka'）。\n"
                "2. 将无标点的文本根据语义进行结构化段落划分。\n"
                "3. 提炼出核心原理、架构设计或核心代码/逻辑片段。\n\n"
                f"原始 ASR 文本：\n{state['raw_text']}"
            )
        else:
            prompt = (
                "你是一个资深的 AI Infrastructure 技术文档专家。请对以下技术视频的原始 ASR 文本进行处理：\n"
                "1. 修正错别字，尤其是专业技术术语（例如将'扣打'修正为'CUDA'，'卡夫卡'修正为'Kafka'，'单子融合'修正为'算子融合'）。\n"
                "2. 将无标点的文本根据语义进行结构化段落划分。\n"
                "3. 提炼出核心原理、架构设计或核心代码/逻辑片段。\n\n"
                f"原始 ASR 文本：\n{state['raw_text']}"
            )
        content = _call_llm(client, model, prompt, temperature=0.2)
        return {"final_markdown": content}
```

- [ ] **Step 3: Modify auto_link_node to use rag_context**

In `src/services/agent_service.py`, replace `auto_link_node` (lines 80-101):

```python
    def auto_link_node(state: AgentState) -> Dict[str, Any]:
        """节点 2：基于已有笔记生成双链。"""
        logger.info("[Agent] 自动织网: %s", state["video_name"])
        notes = state.get("existing_notes", [])
        preferred = notes[:10]
        notes_summary = ", ".join(notes) if notes else "（无已有笔记）"
        preferred_summary = ", ".join(preferred) if preferred else ""

        rag = state.get("rag_context", "")
        if rag:
            prompt = (
                "你是一个高级知识库架构师。\n\n"
                "[知识库检索结果]\n"
                "以下片段可能与当前文本高度相关：\n"
                f"{rag}\n\n"
                "[任务]\n"
                "请在不破坏原有 Markdown 结构的前提下，比对给定的'已有笔记列表'。\n"
                "如果当前技术笔记中出现了列表中已有的概念，请自动将其转换为 Obsidian 双链语法 `[[已存在的笔记]]`。\n"
                "参考上方检索片段中反复出现的笔记名——它们应该优先被链入。\n"
                "如果发现非常关键、且列表里没有的全新技术名词，也请用 `[[新概念]]` 进行前瞻性标记。\n\n"
                f"已有笔记列表：{notes_summary}\n"
            )
        else:
            prompt = (
                "你是一个高级知识库架构师。请在不破坏原有 Markdown 结构的前提下，比对给定的'已有笔记列表'。\n"
                "如果当前技术笔记中出现了列表中已有的概念，请自动将其转换为 Obsidian 双链语法 `[[已存在的笔记]]`。\n"
                "如果发现非常关键、且列表里没有的全新技术名词，也请用 `[[新概念]]` 进行前瞻性标记。\n\n"
                f"已有笔记列表：{notes_summary}\n"
            )

        if preferred_summary:
            prompt += f"\n推荐优先链接（高质量笔记）：{preferred_summary}\n"
        prompt += f"\n当前笔记内容：\n{state['final_markdown']}"
        feedback = state.get("feedback_context", "")
        if feedback:
            prompt += f"\n\n用户反馈建议：\n{feedback}"
        content = _call_llm(client, model, prompt, temperature=0.1)
        return {"final_markdown": content}
```

- [ ] **Step 4: Modify suggest_update_node to use rag_context**

In `src/services/agent_service.py`, replace `suggest_update_node` (lines 103-149):

```python
    def suggest_update_node(state: AgentState) -> Dict[str, Any]:
        """节点 3：分析是否需要更新关联已有笔记。"""
        related = state.get("related_notes", [])
        rag = state.get("rag_context", "")

        if not related and not rag:
            logger.info("[Agent] 无关联笔记，跳过更新建议")
            return {"update_suggestions": []}

        logger.info("[Agent] 生成更新建议 (关联 %d 篇笔记): %s", len(related), state["video_name"])

        related_summary_parts: list[str] = []
        for rn in related:
            preview = rn.get("content_preview", "")
            related_summary_parts.append(
                f"- 笔记: {rn['name']}\n  匹配术语: {', '.join(rn['match_terms'])}\n  内容预览: {preview[:200]}"
            )
        related_summary = "\n".join(related_summary_parts) if related_summary_parts else "（无关联笔记）"

        new_preview = state["final_markdown"][:600]

        if rag:
            prompt = (
                f"Given this NEW note about \"{state['video_name']}\" and EXISTING related notes below, "
                "determine if each existing note should be updated.\n\n"
                "[Knowledge Base Retrieval]\n"
                "The new content is semantically similar to these existing vault chunks:\n"
                f"{rag}\n\n"
                "Options:\n"
                "- 'none': no update needed\n"
                "- 'ref': add a reference link at the bottom\n"
                "- 'supplement': add supplementary content\n\n"
                f"## New Note Content (preview)\n{new_preview}\n\n"
                f"## Existing Related Notes\n{related_summary}\n\n"
                "Output JSON:\n"
                '{"suggestions": [{"target_note": "<name>", "type": "ref|supplement|none", '
                '"content": "markdown text to append"}]}'
            )
        else:
            prompt = (
                f"Given this NEW note about \"{state['video_name']}\" and EXISTING related notes below, "
                "determine if each existing note should be updated.\n\n"
                "Options:\n"
                "- 'none': no update needed\n"
                "- 'ref': add a reference link at the bottom\n"
                "- 'supplement': add supplementary content\n\n"
                f"## New Note Content (preview)\n{new_preview}\n\n"
                f"## Existing Related Notes\n{related_summary}\n\n"
                "Output JSON:\n"
                '{"suggestions": [{"target_note": "<name>", "type": "ref|supplement|none", '
                '"content": "markdown text to append"}]}'
            )

        raw_json = _call_llm(client, model, prompt, temperature=0.1)
        try:
            json_match = _re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_json, _re.DOTALL)
            if json_match:
                raw_json = json_match.group(1)
            result = json.loads(raw_json)
            suggestions = result.get("suggestions", [])
            filtered = [s for s in suggestions if s.get("type", "none") != "none"]
            logger.info("[Agent] 更新建议生成完成: %d 条", len(filtered))
            return {"update_suggestions": filtered}
        except Exception:
            logger.warning("[Agent] 解析更新建议 JSON 失败")
            return {"update_suggestions": []}
```

- [ ] **Step 5: Run existing agent tests to verify no regression**

```
python -m pytest tests/test_agent_service.py -v
```

Expected: All existing tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/models/state.py src/services/agent_service.py
git commit -m "feat: inject RAG context into Agent three-node prompts"
```

---

### Task 5: Pipeline Integration

**Files:**
- Modify: `src/services/pipeline_service.py`

- [ ] **Step 1: Add RAG retrieval before Agent**

In `src/services/pipeline_service.py`, add the import at the top:

```python
from src.services.chunk_service import ChunkStore
```

After the related notes check (after line 176, before the Step 3 Agent comment), add:

```python
        # Step 2.6: RAG retrieval (chunk-level semantic search)
        rag_context = ""
        if cfg.embedding_enabled and embed_engine is not None:
            try:
                chunk_store = ChunkStore(str(vault_path))
                if chunk_store.get_all_note_names():
                    query_vec = embed_engine.embed(raw_text[:1000])
                    results = chunk_store.find_similar(query_vec, top_k=5)
                    if results:
                        context_parts: list[str] = []
                        for r in results:
                            ctx = r.full_context()
                            context_parts.append(
                                f"--- 来源: [[{r.note_name}]] (相似度: {r.similarity:.2f}) ---\n{ctx}"
                            )
                        rag_context = "\n\n".join(context_parts)
                        logger.info(
                            "[Pipeline] RAG 检索: %d 个相关片段", len(results)
                        )
            except Exception as e:
                logger.warning("[Pipeline] RAG 检索失败，降级为无 RAG: %s", str(e))
                rag_context = ""
```

- [ ] **Step 2: Add rag_context to initial_state**

In `src/services/pipeline_service.py`, modify the `initial_state` dict (around line 182-191) to include `rag_context`:

```python
        initial_state: AgentState = {
            "video_id": video_id,
            "video_name": video_name,
            "raw_text": raw_text,
            "existing_notes": existing_notes,
            "related_notes": related_notes_list,
            "update_suggestions": [],
            "final_markdown": "",
            "feedback_context": feedback_context,
            "rag_context": rag_context,
        }
```

- [ ] **Step 3: Add chunk indexing after note write**

After the embedding caching block (after line 301, before Step 4.5 comment), add:

```python
            # Index new note chunks for RAG
            if cfg.embedding_enabled and embed_engine is not None:
                try:
                    chunk_store = ChunkStore(str(vault_path))
                    chunk_store.chunk_note(output_stem, full_content, embed_engine)
                    logger.info("[Pipeline] Chunk 索引: %s", output_stem)
                except Exception as e:
                    logger.warning("[Pipeline] Chunk 索引失败: %s", str(e))
```

- [ ] **Step 4: Run pipeline tests to verify integration**

```
python -m pytest tests/test_pipeline_service.py -v
```

Expected: All existing pipeline tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/services/pipeline_service.py
git commit -m "feat: integrate RAG retrieval and chunk indexing into pipeline"
```

---

### Task 6: CLI --chunk-all Flag

**Files:**
- Modify: `src/cli.py`
- Modify: `src/models/config.py`

- [ ] **Step 1: Add chunk_all to PipelineConfig**

In `src/models/config.py`, add after `embedding_enabled` (currently line 80):

```python
    embedding_enabled: bool = False
    chunk_all: bool = False
```

- [ ] **Step 2: Add --chunk-all CLI argument**

In `src/cli.py`, after the `--embedding` argument (after line 154), add:

```python
    parser.add_argument(
        "--chunk-all",
        action="store_true",
        default=False,
        help="一次性全量盘点 vault 中所有笔记的 chunk（需 --embedding）",
    )
```

- [ ] **Step 3: Wire chunk_all in build_config()**

In `src/cli.py`, in `build_config()` (around line 234), add the new field:

```python
        embedding_enabled=args.embedding,
        chunk_all=args.chunk_all,
```

- [ ] **Step 4: Commit**

```bash
git add src/cli.py src/models/config.py
git commit -m "feat: add --chunk-all CLI flag for full vault chunk indexing"
```

---

### Task 7: Integration & Verification

**Files:**
- Modify: `tests/conftest.py`
- Verify: full test suite + lint

- [ ] **Step 1: Add ChunkStore fixture to conftest**

In `tests/conftest.py`, after the `sample_markdown_content` fixture, add:

```python
@pytest.fixture
def chunk_store_fixture(tmp_path):
    """Create a test ChunkStore with a pre-populated note."""
    from unittest.mock import MagicMock

    from src.services.chunk_service import ChunkStore

    store = ChunkStore(str(tmp_path))
    engine = MagicMock()
    engine.embed_batch.return_value = [[0.1] * 1024, [0.2] * 1024, [0.3] * 1024]
    engine.embed.return_value = [0.1] * 1024

    content = (
        "## CUDA Optimization\n"
        "CUDA kernel fusion reduces kernel launch overhead.\n"
        "It is a key technique for GPU performance.\n\n"
        "## Memory Management\n"
        "Proper memory management avoids fragmentation.\n"
        "Use pinned memory for faster transfers."
    )
    store.chunk_note("TestGPU", content, engine)
    return store, engine
```

- [ ] **Step 2: Run full test suite**

```
python -m pytest tests/ -v --tb=short
```

Expected: All tests pass (existing + new).

- [ ] **Step 3: Run ruff lint check**

```
python -m ruff check src/
```

Fix any issues before proceeding.

- [ ] **Step 4: Commit**

```bash
git add tests/conftest.py
git commit -m "test: add ChunkStore fixture, verify full test suite passes"
```
