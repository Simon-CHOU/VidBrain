# VidBrain 引入 Embedding 的研判与实施方案

## 一、研判结论

**值得引入。** 项目当前有 7 个环节使用字符串/关键词匹配，其中 3 个是明显的语义盲区，用 embedding 替代可产生数量级改进。**推荐本地模型方案（零额外 API 费用），分批实施。**

## 二、7 个潜在用例的优先级矩阵

| 优先级 | 用例 | 当前做法 | 问题严重度 | 嵌入替代方案 | 嵌入调用数 | 核心收益 |
|:---:|:---|:---|:---:|:---|:---:|:---|
| **P0** | `updater.py` 关联笔记检测 | 前200字符提取术语 + 子串匹配 | 高 | 嵌入新笔记内容 → 余弦检索 top-3 | 1次/视频 | 关联质量质变 |
| **P0** | `refiner.py` MOC 聚类 | 全部标题传LLM分组(贵且不准) | 高 | 嵌入笔记内容 → k-means → LLM仅命名 | N次(预计算) | MOC质量+token双赢 |
| **P1** | `agent_graph.py` 双链生成 | 全量笔记名注入prompt(O(N) token) | 中(随规模恶化) | 嵌入预筛选 top-K 笔记名 | 1次/视频 | Token节省80-98% |
| **P1** | `classifier.py` unclear分类 | 纯关键词，语义盲区 | 中 | 标题嵌入 → 相似度分类 | 1次/视频 | 减少人工审核 |
| **P1** | `refiner.py` 孤立链接建议 | LLM做检索(昂贵) | 中 | 嵌入检索替代/辅助LLM | N次(预计算) | 可消除LLM调用 |
| **P1** | `pipeline.py` 知识库扫描 | 全量笔记名传递 | 中 | 嵌入预筛选相关笔记 | 1次/视频 | 与P1.1协同 |
| **P2** | `feedback.py` 编辑信号 | 仅统计链接频率 | 低 | 嵌入对比原始/编辑后内容 | 2次/编辑笔记 | 锦上添花 |

## 三、推荐方案：本地模型

| 决策项 | 选择 | 理由 |
|:---|:---|:---|
| **嵌入模型** | `sentence-transformers` + `BAAI/bge-small-zh-v1.5` | 中文友好、~100MB、零API费用、5ms/次 |
| **依赖** | `pip install sentence-transformers` | pyproject.toml 新增一项 |
| **向量存储** | Vault 内 `.vidbrain_embeddings.json` | 自包含、无需外部数据库、人类可读 |
| **检索** | 纯 numpy 余弦相似度（brute-force） | 笔记量 < 5000 时性能足够 |

## 四、架构设计

### 4.1 新增模块 `vidbrain/embedding.py`

```
embedding.py
├── EmbeddingEngine        # 单例，加载 BGE 模型，提供 embed() 方法
├── EmbeddingStore         # 读写 .vidbrain_embeddings.json
├── find_similar_notes()   # 给定查询文本，返回 top-K 最相似笔记
├── cluster_by_embedding() # k-means 聚类，返回 {topic_id: [note_names]}
└── compute_similarity()   # 两段文本的嵌入余弦相似度
```

### 4.2 嵌入存储格式（`.vidbrain_embeddings.json`）

```json
{
  "meta": {"model": "bge-small-zh-v1.5", "updated": "2026-06-07T15:00:00", "dim": 512},
  "vectors": {
    "note_stem_1": [0.12, -0.34, ...],
    "note_stem_2": [0.08, 0.22, ...]
  }
}
```

- 增量更新：新笔记写入时计算嵌入并追加
- 失效检测：笔记 mtime > 缓存中记录的 mtime → 重新计算
- 自包含：JSON 放在 vault 根目录，随 vault 一起备份/迁移

### 4.3 数据流

```
vault_dir/
├── note1.md
├── note2.md
├── MOC-xxx.md
├── _drafts/
└── .vidbrain_embeddings.json   ← 嵌入缓存
```

## 五、分阶段实施

### Phase A（P0，约 150 行代码）：updater 关联 + MOC 聚类

**改动 `vidbrain/updater.py`**：
- `check_related_notes()` 中 `_match_notes()`（子串匹配）替换为 embedding 余弦检索
- 从 "前 200 字符提取术语" 改为 "对全文前 1000 字符做嵌入 → 与 vault 嵌入库做相似度检索 → 返回 top-3"

**改动 `vidbrain/refiner.py`**：
- `_extract_topics()` 中 LLM 聚类替换为 `cluster_by_embedding()`（k-means）
- LLM 仅用于命名每个簇（3-8 次微型 prompt 替代 1 次巨型 prompt）

### Phase B（P1，约 100 行代码）：Agent 双链 + 分类器 + 管线筛选

**改动 `vidbrain/agent_graph.py`**：
- `auto_link_node` 中全量 `existing_notes` 改为 top-K embedding 筛选结果

**改动 `vidbrain/classifier.py`**：
- `unclear` 兜底增加 embedding 相似度分类（标题嵌入 vs "tech"/"skip" 示例嵌入）

**改动 `vidbrain/pipeline.py`**：
- Step 2 扫描后用 embedding 预筛选相关笔记，减少传给 Agent 的数量

### Phase C（P2，约 80 行代码）：Refiner 链接 + 反馈信号

**改动 `vidbrain/refiner.py`**：
- `_call_llm_batch()` 中用 embedding 检索替代部分 LLM 判定

**改动 `vidbrain/feedback.py`**：
- `extract_feedback_signals()` 增加原始/编辑后内容嵌入对比

## 六、自包含性分析

| 维度 | 影响 |
|:---|:---|
| **新增依赖** | `sentence-transformers`（会自动拉 `torch`、`transformers`）→ ~2GB venv 膨胀 |
| **模型文件** | BGE-small-zh 约 100MB，首次运行自动下载到 HF cache（已在 `.model_cache/`） |
| **新增文件** | 仅 `.vidbrain_embeddings.json`（每个 vault 一份） |
| **内存** | BGE 模型加载后约 200MB 常驻内存 |
| **启动时间** | 首次加载模型约 3-5 秒 |

**注意：`sentence-transformers` 依赖 torch，会使 venv 膨胀约 2GB。如果这个成本不可接受，备选方案是调用用户的 embedding vendor API 替代本地模型。**

## 七、与现有架构的关系

| 现有模块 | Phase A 改动 | Phase B 改动 | Phase C 改动 |
|:---|:---:|:---:|:---:|
| `updater.py` | 替换 `_match_notes` | — | — |
| `refiner.py` | 替换 `_extract_topics` | — | 替换 `_call_llm_batch` |
| `agent_graph.py` | — | 替换 `notes_summary` 构建 | — |
| `classifier.py` | — | 增加 `unclear` embedding 兜底 | — |
| `pipeline.py` | — | 增加 Step 2.2 嵌入预筛选 | — |
| `feedback.py` | — | — | 增加嵌入对比 |
| `embedding.py` | **新增** | 扩展 | 扩展 |

## 八、验证

1. **语法检查**：`uv run python -m py_compile` 所有修改文件
2. **单元测试**：`uv run pytest vidbrain/tests/ -v` 全部通过
3. **功能验证**：用 smoke_test_vault 跑一次 `--once`，验证嵌入缓存生成、相似度检索返回合理结果
4. **性能基准**：对比 embedding 方案 vs 原始方案的关联检测准确率（人工标注 10 对笔记）

## 九、实施优先级建议

1. **Phase A 先做**（updater + MOC）：改动最小（2 个模块），收益最大（关联质量 + MOC 质量），风险最低
2. **Phase B 后做**（Agent 双链 + 分类器）：需要 Phase A 的嵌入基础设施就绪，主要节省 token 成本
3. **Phase C 可选**（Refiner 链接 + 反馈）：锦上添花，可在系统稳定运行一段时间后根据实际成本数据决定是否实施

## 十、风险与注意事项

1. **BGE 模型对中英混合文本的嵌入质量**：BGE-small-zh 主要针对中文优化，英文技术术语的嵌入效果需验证
2. **k-means 聚类 K 值选择**：需要自适应 K 值（如 elbow method 或 silhouette score），避免固定 K 值
3. **嵌入缓存失效策略**：笔记内容变更后需重新计算嵌入，需监听 mtime 变化
4. **vendor API 作为备选**：如果本地模型效果不符合预期，可回退到用户的 embedding vendor API
