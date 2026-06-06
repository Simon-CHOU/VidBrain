# Tasks

- [ ] Task 1: 新增 `EmbeddingConfig` 到 `vidbrain/config.py`
  - [ ] 1.1 创建 `EmbeddingConfig` dataclass，从 `DASHSCOPE_API_KEY` / `DASHSCOPE_BASE_URL` 环境变量读取
  - [ ] 1.2 默认 model = `"text-embedding-v4"`，默认 base_url = `"https://dashscope.aliyuncs.com/compatible-mode/v1"`
  - [ ] 1.3 `__post_init__` 中 API Key 缺失时抛出 OSError

- [ ] Task 2: 创建 `vidbrain/embedding.py` 模块
  - [ ] 2.1 实现 `EmbeddingEngine` 类
    - `__init__(config)` — 创建 OpenAI 客户端（复用 openai 依赖）
    - `embed(text) -> list[float]` — 单文本嵌入，3 次指数退避重试
    - `embed_batch(texts) -> list[list[float]]` — 批量嵌入（每批最多 25）
    - `similarity(vec1, vec2) -> float` — 余弦相似度（纯 numpy 或自实现）
  - [ ] 2.2 实现 `EmbeddingStore` 类
    - `__init__(vault_path)` — 初始化，调用 `load()`
    - `load()` — 从 `.vidbrain_embeddings.json` 读取 meta/mtime/vectors
    - `save()` — 持久化到 `.vidbrain_embeddings.json`
    - `get_vector(stem) -> list[float] | None`
    - `set_vector(stem, vec, mtime_str)`
    - `needs_recompute(stem, file_mtime) -> bool` — 比较缓存 mtime vs 文件 mtime
    - `all_stems() -> list[str]`
    - `find_similar(query_vec, top_k=5) -> list[tuple[str, float]]` — O(N) 余弦检索
  - [ ] 2.3 实现 `_kmeans(vectors, k, max_iter=100) -> list[int]` — 纯 numpy k-means
    - 随机初始化质心（seed=42 保证可复现）
    - 迭代直到收敛或 max_iter
    - 返回每个向量的簇标签

- [ ] Task 3: 修改 `vidbrain/config.py` 添加 `embedding_enabled` 字段
  - [ ] 3.1 `PipelineConfig` 新增 `embedding_enabled: bool = False`

- [ ] Task 4: 修改 `vidbrain/updater.py` — embedding 关联检测
  - [ ] 4.1 `check_related_notes()` 增加条件分支：若 `cfg.embedding_enabled` 且 `EmbeddingStore` 中有缓存，使用 embedding 检索
  - [ ] 4.2 embedding 路径：embed new_content[:1000] → find_similar(top_k=5) → 过滤相似度 < 0.5 → 取 top-3 → 读取 content_preview
  - [ ] 4.3 签名新增 `embedding_enabled: bool = False` 和 `embedding_store: EmbeddingStore | None = None` 参数
  - [ ] 4.4 默认参数保持向后兼容（不传 embedding 参数 = 旧行为）

- [ ] Task 5: 修改 `vidbrain/refiner.py` — embedding MOC 聚类
  - [ ] 5.1 `_extract_topics()` 增加条件分支：若 `embedding_store` 非空且缓存覆盖率 >= 50%，使用 k-means 聚类
  - [ ] 5.2 embedding 路径：从 EmbeddingStore 获取所有笔记向量 → 缺失的用 embed_batch 批量计算 → k-means(K=min(8, max(3, len//5))) → LLM 命名每个簇
  - [ ] 5.3 回退路径：缓存覆盖率 < 50% 时使用原始 LLM-only 方案
  - [ ] 5.4 函数签名新增可选参数 `embedding_config: EmbeddingConfig | None = None` 和 `embedding_store: EmbeddingStore | None = None`

- [ ] Task 6: 修改 `vidbrain/pipeline.py` — 管线集成 EmbeddingStore
  - [ ] 6.1 `process_pipeline` 新增可选参数 `embedding_config: EmbeddingConfig | None = None`、`embedding_store: EmbeddingStore | None = None`
  - [ ] 6.2 当 `cfg.embedding_enabled` 时，Step 2 初始化 `EmbeddingStore`（如果未传入）并传给 `check_related_notes`
  - [ ] 6.3 Step 4 写入新笔记后调用 `embedding_store.set_vector(stem, ...)` 增量缓存嵌入

- [ ] Task 7: 修改 `vidbrain/main.py` — CLI 集成
  - [ ] 7.1 新增 `--embedding` CLI flag（action='store_true'，默认 False）
  - [ ] 7.2 `build_config` 映射 `embedding_enabled=args.embedding`
  - [ ] 7.3 `main()` 中，若 `cfg.embedding_enabled`，初始化 `EmbeddingConfig` 和 `EmbeddingStore`，传给 `process_batch` 和 `run_refine`
  - [ ] 7.4 `process_batch` 签名新增 `embedding_config` 和 `embedding_store` 参数，透传给 `process_pipeline`
  - [ ] 7.5 `run_refine` 签名新增 `embedding_config` 和 `embedding_store` 参数，透传给 `refine_vault`

- [ ] Task 8: 编写测试
  - [ ] 8.1 `tests/test_embedding.py` — `EmbeddingStore` 单元测试（load/save/get/set/find_similar/needs_recompute），使用临时目录
  - [ ] 8.2 `tests/test_embedding.py` — `_kmeans` 单元测试（3 簇明显分离的向量应被正确分类）
  - [ ] 8.3 `tests/test_embedding.py` — `EmbeddingEngine.similarity` 单元测试（相同向量=1.0，正交向量≈0）
  - [ ] 8.4 运行全量回归测试确保旧路径不受影响

# Task Dependencies

- Task 2 依赖 Task 1（EmbeddingEngine 需要 EmbeddingConfig）
- Task 4 依赖 Task 2（updater 需要 EmbeddingEngine + EmbeddingStore）
- Task 5 依赖 Task 2（refiner 需要 EmbeddingEngine + EmbeddingStore）
- Task 6 依赖 Task 4（pipeline 调用 updater）
- Task 7 依赖 Task 1、Task 6
- Task 3 与 Task 1、Task 2 并行
- Task 8 与 Task 4-7 并行
