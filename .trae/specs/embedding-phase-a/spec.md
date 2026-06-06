# Embedding Phase A Spec

## Why

当前 `updater.py` 用子串匹配检测关联笔记——"Attention" 不会匹配 "注意力机制"、"KV缓存" 不会匹配 "KV-Cache"。`refiner.py` 的 MOC 生成把全部标题塞给 LLM 做聚类，token 成本 O(N) 且聚类质量差。引入 embedding 替换这两个环节，关联检测准确率和 MOC 聚类质量均获数量级提升。

## What Changes

- **新增** `vidbrain/embedding.py`：EmbeddingConfig + EmbeddingEngine + EmbeddingStore
- **修改** `vidbrain/updater.py`：`check_related_notes()` 中 `_extract_key_terms` + `_match_notes` 替换为 embedding 余弦检索
- **修改** `vidbrain/refiner.py`：`_extract_topics()` 中 LLM 聚类替换为 embedding k-means → LLM 仅命名
- **修改** `vidbrain/config.py`：新增 `EmbeddingConfig` dataclass

## Impact

- Affected specs: updater 关联检测、refiner MOC 生成
- Affected code: `embedding.py` (NEW), `config.py`, `updater.py`, `refiner.py`
- **BREAKING**: 无。旧版 `_extract_key_terms` / `_match_notes` 函数保留不动，新增 embedding 作为高层替代

## ADDED Requirements

### Requirement: EmbeddingConfig

系统 SHALL 提供 `EmbeddingConfig` dataclass，从环境变量读取 DashScope embedding API 配置。

```python
@dataclass
class EmbeddingConfig:
    api_key: str = field(init=False)
    base_url: str = field(init=False)
    model: str = "text-embedding-v4"  # DashScope 嵌入模型

    def __post_init__(self) -> None:
        self.api_key = os.environ.get("DASHSCOPE_API_KEY", "")
        self.base_url = os.environ.get("DASHSCOPE_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1")
        if not self.api_key:
            raise OSError("环境变量 DASHSCOPE_API_KEY 未设置")
```

#### Scenario: API Key 缺失
- **WHEN** `DASHSCOPE_API_KEY` 环境变量未设置
- **THEN** 抛出 `OSError`，提示用户设置环境变量

#### Scenario: API Key 存在
- **WHEN** `DASHSCOPE_API_KEY` 已设置
- **THEN** `EmbeddingConfig.__post_init__` 正常完成
- **AND** `base_url` 使用默认 DashScope compatible URL（若有 `DASHSCOPE_BASE_URL` 环境变量则覆盖）

### Requirement: EmbeddingEngine

系统 SHALL 提供 `EmbeddingEngine` 类，封装 embedding API 调用。

```python
class EmbeddingEngine:
    def __init__(self, config: EmbeddingConfig) -> None:
        self._client = OpenAI(api_key=config.api_key, base_url=config.base_url)

    def embed(self, text: str) -> list[float]:
        """将文本嵌入为向量。含 3 次指数退避重试。"""
    
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """批量嵌入，一次 API 调用处理最多 25 篇文本。"""
    
    def similarity(self, vec1: list[float], vec2: list[float]) -> float:
        """余弦相似度 [-1, 1]"""
```

#### Scenario: embed 单个文本
- **WHEN** 调用 `engine.embed("some text")`
- **THEN** 返回 `list[float]`，维度 >= 1024
- **WHEN** API 调用失败
- **THEN** 自动重试最多 3 次，指数退避 1s/2s/4s

#### Scenario: embed_batch 批量文本
- **WHEN** 调用 `engine.embed_batch(["a", "b", ...])`，文本数 <= 25
- **THEN** 一次 API 调用嵌入所有文本，返回 `list[list[float]]`

### Requirement: EmbeddingStore

系统 SHALL 提供 `EmbeddingStore` 类，管理 vault 中的嵌入缓存。

**存储格式** (`vault_dir/.vidbrain_embeddings.json`):
```json
{
  "meta": {"model": "text-embedding-v4", "updated": "2026-06-07T15:00:00", "dim": 1024},
  "mtime": {"note_stem": "2026-06-07T12:00:00"},
  "vectors": {"note_stem": [0.12, -0.34, ...]}
}
```

**API**:
```python
class EmbeddingStore:
    def load(self) -> None                          # 从 JSON 加载所有向量
    def get_vector(self, stem: str) -> list[float] | None   # 获取单个向量
    def set_vector(self, stem: str, vector: list[float], mtime: str) -> None  # 存储
    def save(self) -> None                          # 持久化到 JSON
    def needs_recompute(self, stem: str, file_mtime: str) -> bool  # 是否需要重新计算
    def all_stems(self) -> list[str]                # 所有已缓存的笔记 stem
    def find_similar(self, query_vec: list[float], top_k: int = 5) -> list[tuple[str, float]]  # 余弦检索 top-K
```

#### Scenario: 增量缓存
- **WHEN** 新笔记写入 vault 后调用 `set_vector(stem, vec, mtime)`
- **THEN** 向量存入内存 dict，调用 `save()` 后持久化到 JSON

#### Scenario: 失效检测
- **WHEN** 笔记文件 mtime > 缓存中记录的 mtime
- **THEN** `needs_recompute()` 返回 True，下次调用会重新计算嵌入

#### Scenario: 相似检索
- **WHEN** 调用 `find_similar(query_vec, top_k=5)`
- **THEN** 返回相似度最高的 5 个 (stem, similarity) 元组，按相似度降序
- **WHEN** vault 中无笔记
- **THEN** 返回空列表

### Requirement: updater 关联检测替换

`check_related_notes()` SHALL 使用 embedding 替代子串匹配来检测关联笔记。

#### Scenario: embedding 关联检测
- **WHEN** `check_related_notes(vault_path, new_note_name, new_content, existing_notes)` 被调用
- **THEN** 使用 `EmbeddingEngine.embed(new_content[:1000])` 获取新笔记嵌入向量
- **AND** 使用 `EmbeddingStore.find_similar(query_vec, top_k=5)` 检索最相似笔记
- **AND** 过滤相似度 < 0.5 的结果
- **AND** 返回 top-3，每项含 name, stem, match_terms(空列表), score(相似度), content_preview(前400字符)

#### Scenario: 无已有笔记
- **WHEN** existing_notes 为空或 EmbeddingStore 为空
- **THEN** 返回空列表（不调用 embedding API）

#### Scenario: 新笔记内容极短
- **WHEN** new_content 长度 < 50 字符
- **THEN** 跳过 embedding 检索，返回空列表

### Requirement: refiner MOC 聚类替换

`_extract_topics()` SHALL 使用 embedding k-means 聚类替代纯 LLM 分类。

#### Scenario: embedding 聚类
- **WHEN** `_extract_topics(client, model, notes)` 被调用
- **THEN** 对每篇笔记内容（前 800 字符）调用 `EmbeddingStore.get_vector()` 获取嵌入
- **AND** 对缺失的笔记调用 `EmbeddingEngine.embed_batch()` 批量计算并缓存
- **AND** 使用固定 K=min(8, max(3, len(notes)//5)) 做 k-means 聚类（纯 numpy，无外部依赖）
- **AND** 对每个簇，取最接近质心的笔记名作为簇代表，调用 LLM 生成主题名和描述
- **AND** 返回 `[{"topic": str, "notes": [str], "description": str}]`

#### Scenario: 笔记量 < 3
- **WHEN** notes 数量 < 3
- **THEN** 不聚类，所有笔记归为一个主题，LLM 生成主题名

#### Scenario: k-means 收敛失败
- **WHEN** k-means 未能在 100 次迭代内收敛
- **THEN** 使用最后一次迭代的结果（不报错，继续）

#### Scenario: 纯 LLM 回退
- **WHEN** EmbeddingStore 中缓存的向量数 < 总笔记数的 50%
- **THEN** 回退到原始 LLM-only 方案（`_extract_topics` 的旧行为），确保 MOC 总能生成

### Requirement: CLI 可选启用

系统 SHALL 通过 `--embedding` flag 控制是否启用 embedding 功能。

#### Scenario: --embedding 启用
- **WHEN** 用户运行 `--embedding` flag
- **THEN** `PipelineConfig.embedding_enabled = True`，启用 embedding 关联检测和 MOC 聚类

#### Scenario: 默认不启用
- **WHEN** 用户未提供 `--embedding`
- **THEN** `PipelineConfig.embedding_enabled = False`，使用旧的子串匹配和 LLM 聚类（向后兼容）

## MODIFIED Requirements

无。所有现有功能保持不变，embedding 作为可选增强通过 `--embedding` flag 启用。

## REMOVED Requirements

无。`_extract_key_terms` 和 `_match_notes` 函数保留不动，embedding 方案在 `check_related_notes()` 中作为条件分支调用。
