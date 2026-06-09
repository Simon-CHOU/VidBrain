# RAG 增强 Agent 设计文档

## 目标

将 Embedding 检索能力注入 VidBrain 管道的 Agent 节点，使 LLM 在处理每条新知识时能对照已有知识库的相关片段，持续提升输出质量。知识库随内容增长自我进化——更好的处理输出产生更好的知识库，更好的知识库提供更精准的检索上下文，形成正向飞轮。

## 架构概览

```
现有流程：  ASR文本 → LLM (纠错/链接/更新)

改为：      ASR文本
              → Embedding检索 (从vault chunk索引中查top-k相关片段)
              → 相关片段 + 左右邻居chunks + ASR文本 拼接成 prompt
              → LLM (纠错/链接/更新)
              → 写入vault后更新chunk索引
```

三层结构：

- **Chunk 分片层**：按 `##`/`###` 标题 + 段落边界切分笔记为语义片段
- **Chunk 索引层**：SQLite 存元数据 + numpy 存向量，启动时渐进盘点存量笔记
- **Agent 注入层**：检索结果拼入三节点 prompt，命中 chunk 自动附带左右邻居

---

## Chunk 分片层

### 分片边界（优先级从高到低）

1. `##` 二级标题 — 最强边界，一定切开
2. `###` 三级标题 — 次强边界
3. 空行（段落边界）— 仅在 chunk 过长时切开
4. 句子边界（`。！？\n`）— 兜底，不在句子中间切断

### Chunk 大小

- 目标：300-600 字符/chunk
- 最小：150 字符（更小的不单独成 chunk，合并到相邻 chunk）
- 最大：800 字符（超过在段落边界处切断）

### Chunk 元数据

```python
{
    "chunk_id": "Transformer 架构详解#2",
    "note_name": "Transformer 架构详解",
    "content": "## KV-Cache 加速推理\n解码时...",
    "prev_chunk_id": "Transformer 架构详解#1",
    "next_chunk_id": None,
    "token_count": 250
}
```

---

## Chunk 索引层

### 存储方案

- **SQLite**：存 chunk 元数据（chunk_id、note_name、mtime），复用项目已有依赖
- **numpy `.npy` 文件**：存向量矩阵，按 chunk_id 顺序对齐。全量加载到内存后一次 `np.dot` 完成余弦相似度计算

### 存储文件

```
vault/
├── .vidbrain_chunks.db      # SQLite，chunk 元数据 + mtime 变更检测
├── .vidbrain_chunk_vectors.npy  # numpy 二进制，向量矩阵
└── .vidbrain_embeddings.json    # 保留不动，旧笔记级 embedding，兼容现有功能
```

### 核心 API

```python
class ChunkStore:
    def chunk_note(note_name: str, content: str) -> None
    def remove_note(note_name: str) -> None
    def find_similar(query_vec: list[float], top_k: int = 5) -> list[ChunkContext]
    def is_stale(note_name: str, current_mtime: float) -> bool
```

### ChunkContext（检索返回结构）

```python
@dataclass
class ChunkContext:
    chunk_id: str
    note_name: str
    content: str            # 命中 chunk 的内容
    left_context: str | None     # 左邻居内容
    right_context: str | None    # 右邻居内容
    similarity: float

    def full_context(self) -> str:
        """左邻居 + 命中chunk + 右邻居"""
```

### 变更检测

每次管道启动时全量扫描 vault，对比文件 mtime 与 SQLite 中记录：

- mtime 相同 → 跳过
- mtime 不同 → 重新 chunk + embed

### 渐进式存量盘点

首次启用时，不一次性全量 embed，改为启动时分批处理：

1. 扫描 vault，列出所有未 chunk 的笔记
2. 每批 20 篇，chunk + embed，写入索引
3. 每批完成后让出控制权给正常管道
4. 剩余笔记留到下次启动继续
5. `--chunk-all` 标志一次性全量盘点

---

## Agent 注入层

### 通用规则

- 检索结果上限 5 个
- 相似度 < 0.3 的结果直接丢弃
- 每个结果自动附带左右邻居 chunk
- 检索结果放在 system prompt 中（LLM 内化为知识使用，而非引用来源）
- 检索失败（API 挂）时静默降级，返回空列表，Agent 按原 prompt 运行

### 节点 1：clean_and_extract（纠错 + 分段）

检索时机：ASR 转录完成后，Agent 调用前
检索 query：ASR 文本前 1000 字符
注入内容：top-3 相关 chunk 的完整上下文

Prompt 结构：
```
[系统参考]
从知识库中检索到以下相关片段：
<top-3 chunk 完整上下文>

[任务]
根据以上参考，纠正术语错误。参考片段中的术语写法比 ASR 文本更准确时优先采用。

ASR 文本：
<ASR文本>
```

### 节点 2：auto_link（自动双链）

检索时机：逐段检索（按 `##` 段落），非全文一次检索
注入内容：top-5 chunk 的完整上下文

Prompt 结构：
```
[知识库检索结果]
以下片段可能与当前文本相关：
<top-5 chunk 完整上下文>

[任务]
将文本中提到的概念与知识库笔记双链关联。参考片段中反复出现的笔记优先链入。

现有笔记列表：<所有笔记标题>
文本：<当前段落>
```

### 节点 3：suggest_update（建议更新已有笔记）

检索时机：ASR 文本按 `##` 分段后，逐段检索
注入内容：top-5 chunk 的完整上下文

Prompt 结构：
```
[知识库检索]
新内容与以下已有笔记片段高度相关：
<top-5 chunk 完整上下文>

[任务]
逐一判断每个相关片段对应的笔记是否需要更新：
- 新内容印证已有说法 → 加引用链接
- 新内容补充缺失细节 → 写补充内容
- 新内容与已有说法矛盾 → 标记待人工审查

新内容：<ASR文本>
```

---

## 管道集成

### 触发位置

```
管道：ASR → [RAG检索] → Agent三节点 → 写入vault → [RAG索引更新]
```

- 头部（Agent 前）：检索 vault 片段，注入 prompt
- 尾部（写入后）：新笔记 chunk + embed，写入索引

### 启动流程补充

main.py 启动时在首次处理视频前加入：

1. 扫描 vault 所有 `.md` 文件
2. 对比 chunk 索引中的 mtime
3. 将未 chunk 的笔记加入渐近盘点队列（最多 20 篇/次）
4. 每批 embed 完成后进入正常管道循环
5. `--chunk-all` 一次性全量盘点剩余笔记

---

## 错误处理 & 边界情况

| 情况 | 处理 |
|------|------|
| DashScope API 挂了（检索阶段） | 静默降级，返回空列表，Agent 照原 prompt 运行 |
| embed API 返回 429（限流） | 指数退避重试 3 次，仍失败则跳过当前批次 |
| `.vidbrain_chunk_vectors.npy` 损坏 | 删除文件，触发重新盘点 |
| chunk 切分后结果为空（极短笔记） | 整篇笔记视为一个 chunk |
| vault 为空（首次运行） | 跳过检索，Agent 按原 prompt 运行，新笔记写入后才建索引 |
| 笔记在管道期间被用户编辑 | mtime 已变 → stale 检测 → 下次运行自动重建 |

---

## 不做

- 不删除现有 `.vidbrain_embeddings.json` 和 `EmbeddingStore`，保留兼容
- 不加新 CLI 参数（除 `--chunk-all`），`--embedding` 是唯一开关
- 不改 Agent 三节点逻辑本体，仅改 prompt 模板

---

## 决策记录

| 决策 | 选择 | 理由 |
|------|------|------|
| 存储方案 | SQLite + numpy | 规模不超过几万 chunk，全量矩阵乘法 < 50ms，无需向量数据库 |
| 存量盘点 | 启动时渐进（20 篇/批） | 无人值守 + 全量目标 + 不影响正常管道 |
| 检索粒度 | chunk 级，附带左右邻居 | 即非整篇噪音也非孤立片段，保留因果上下文 |
| 分片边界 | `##` > `###` > 空行 > 句子 | 利用 Obsidian 笔记天然结构 |
| chunk 大小 | 300-600 字符 | 中文约 150-300 字，一个知识点粒度 |
