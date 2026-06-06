# VidBrain 架构演进实施计划

## 一、背景

基于 [自演化知识库差距分析](./self-evolving-gap-analysis.md) 的路线图，Phase 1（精炼自动化 + 失败重试）已完成。当前需要实施 Phase 2 和 Phase 3，实现知识库从"被动收录"到"主动演化"的架构跃迁。

## 二、当前状态

| 已完成 | 状态 |
|:---|:---:|
| 全自动管线（watchdog + ASR + Agent + Vault 输出） | ✅ |
| 半自动模式（--semi + 分类审核 + 队列审批 + 草稿审核） | ✅ |
| 知识库精炼（refiner: 双链补充 + MOC 生成） | ✅ |
| 失败重试 + 自动精炼调度 | ✅ |
| **增量内容更新**（Phase 2 核心） | ❌ |
| **用户反馈闭环**（Phase 3 智能增强） | ❌ |
| **质量评分体系**（Phase 3 智能增强） | ❌ |

## 三、目标架构

### 3.1 Phase 2：增量内容更新（Gap 2）

**问题**：新视频处理完成后只写新笔记，从来不影响旧笔记。早期笔记的术语错误、缺失链接、不完整的知识永远得不到修正。

**目标**：当新视频内容与已有笔记高度相关时，自动更新已有笔记，形成"知识沉积"效应。

**策略**（三级递进，安全优先）：
1. **L1 补充引用**：在已有笔记末尾追加 `> 新增相关视频: [[新笔记]]` 一行引用
2. **L2 术语修正**：若新笔记中同一术语有更准确的表述，在旧笔记中替换（需置信度 > 0.8）
3. **L3 内容融合**：LLM 判断新旧内容可融合时，在旧笔记中追加"补充说明"段落

### 3.2 Phase 3a：用户反馈闭环（Gap 3）

**问题**：用户在 Obsidian 中编辑笔记、修正术语、删除错误双链，系统完全不知情。犯过的错误会重复犯。

**目标**：检测用户对 auto-generated 笔记的编辑行为，从中提取反馈信号，优化后续 Agent 行为。

### 3.3 Phase 3b：质量评分体系（Gap 4）

**问题**：所有笔记在系统中地位平等，无法区分高质量与低质量产出。

**目标**：建立多维质量评分，让系统能优先级修复低质量笔记，高质量笔记在双链推荐中权重更高。

### 3.4 附带修复

| # | 问题 | 改动 |
|---|------|------|
| F1 | `pipeline.py:49` 用 `glob("*.md")` 忽略子目录笔记 | 统一为 `rglob("*.md")`，与 refiner 一致 |
| F2 | `pipeline.py:49` 扫描了 `_drafts/` 下草稿（不应参与双链上下文） | 过滤 `_drafts/` 前缀 |

## 四、详细方案

### Phase 2：增量内容更新

#### 4.1 新模块 `vidbrain/updater.py`

```
updater.py
├── check_related_notes()   # 检索 vault 中与新视频主题相关的已有笔记
├── suggest_update()         # 调用 LLM 生成更新建议
└── apply_update()           # 安全地应用更新到笔记文件
```

**工作流程**：
1. 新视频 Agent 处理完成后，提取其核心关键词（从 final_markdown 中抽取）
2. 用关键词检索 vault 中标题匹配的已有笔记（top 5）
3. 读入匹配笔记的前 500 字符做语义相关性 LLM 判断
4. 若关联度 > 阈值，调用 LLM 生成更新建议（引用补充 / 术语修正 / 内容融合）
5. `apply_update()` 写入修改，记录修订历史

#### 4.2 改动 Agent 工作流（agent_graph.py）

新增第三个 Agent 节点 `suggest_update_node`：
```python
AgentState 新增字段：
    related_notes: List[dict]   # [{"name": str, "content_preview": str, "score": float}]
    update_suggestions: List[dict]  # [{"target_note": str, "type": "ref|fix|merge", "content": str}]
```

在 clean_and_extract → auto_link 之后插入 suggest_update_node。

#### 4.3 改动管线（pipeline.py）

在 Step 4（写入 Vault）之后新增 Step 4.5：
- 仅在 `cfg.semi=False` 时自动执行（半自动模式下增量更新暂不启用，待 user 审核草稿后再考虑）
- 调用 `updater.check_and_update()` 尝试更新相关笔记

#### 4.4 安全约束

- 所有更新操作记录到 `vidbrain_log_updates` 新表，包含操作前后内容快照
- 更新仅在笔记末尾或标记位置追加，不删除原有内容（L1/L2/L3 均为追加性质）
- 每次最多更新 3 篇相关笔记，限制 API 消耗

### Phase 3a：用户反馈闭环

#### 4.5 新模块 `vidbrain/feedback.py`

```
feedback.py
├── detect_user_edits()      # 比对 front_matter 时间与文件修改时间
├── extract_feedback()       # 分析 diff：哪些双链被删？哪些被新增？哪些术语被修正？
└── get_feedback_context()   # 生成 Agent prompt 增量（用户偏好）
```

**反馈信号类型**：
| 信号 | 检测方式 | 含义 |
|:---|:---|:---|
| 删除双链 | diff 旧笔记中 `[[X]]` 在新笔记中不存在 | X 不应被链接 |
| 新增双链 | diff 新笔记中新增 `[[Y]]` 在旧笔记中不存在 | Y 应该被链接 |
| 术语修正 | 文本相似但关键术语变化 | 原术语表述有误 |
| 内容重写 | 大段文本替换，相似度 < 0.5 | 原 Agent 输出质量低 |
| 直接删除 | 文件不再存在 | 分类可能有误 |

#### 4.6 改动 Agent 工作流

在 `auto_link_node` 的 prompt 中注入反馈上下文：
```
用户偏好链接（基于历史编辑行为）：
- 应优先链接的笔记: [[A]], [[B]]
- 不应链接的笔记: [[C]]
```

#### 4.7 改动管线扫描

`pipeline.py` Step 2 扫描 vault 时：
- 读取每篇笔记的 front-matter（`status: auto-generated`, `created`, `reviewed`）
- 对比文件 `mtime`，若 `mtime > created + 1min`，标记为"已被用户编辑"
- 编辑过的笔记纳入 `feedback_context`，在 Agent prompt 中插入

### Phase 3b：质量评分体系

#### 4.8 DB 扩展

```sql
ALTER TABLE video_pipeline ADD COLUMN quality_score REAL;
```

或在 front-matter 中新增：
```yaml
quality:
  score: 7.2
  dimensions:
    asr_segments: 45      # ASR 转录段数越多，信息量越大
    structured: true       # Agent 输出是否有结构化分段
    link_density: 3        # 双链数量 / 笔记长度
    user_edited: true      # 用户是否编辑过（正向信号）
    reviewed: true         # 是否经过人工审核（半自动模式）
```

#### 4.9 改动 Refiner

- Refiner 的双链补充优先推荐高质量笔记（quality_score 高）
- Refiner 的孤立检测优先处理低质量笔记

#### 4.10 改动 Agent auto_link_node

- `existing_notes` 列表按 quality_score 排序，高质量笔记排在前面
- prompt 中标注"推荐优先链接的笔记"（score > 7 的）

## 五、实施任务拆分

### Task 1：修复扫描不一致（F1 + F2）
- [ ] 1.1 `pipeline.py` 扫描 vault 改用 `rglob("*.md")`
- [ ] 1.2 `pipeline.py` 扫描 vault 时过滤 `_drafts/` 路径

### Task 2：创建 updater.py 模块（Phase 2 核心）
- [ ] 2.1 实现 `check_related_notes()` — 基于标题关键词检索 vault 中相关笔记
- [ ] 2.2 实现 `suggest_update()` — 调用 LLM 对每对(新笔记, 旧笔记)生成更新建议
- [ ] 2.3 实现 `apply_update()` — 安全地将建议写入旧笔记末尾
- [ ] 2.4 编写 `tests/test_updater.py` 单元测试

### Task 3：扩展 Agent 工作流（Phase 2）
- [ ] 3.1 `AgentState` 新增 `related_notes` 和 `update_suggestions` 字段
- [ ] 3.2 新增 `suggest_update_node` — 分析新旧内容关联度，生成更新建议
- [ ] 3.3 在 `create_agent_graph` 中插入新节点到 auto_link 之后
- [ ] 3.4 Agent 工作流返回 `update_suggestions` 供管线使用

### Task 4：管线集成更新器（Phase 2）
- [ ] 4.1 `process_pipeline` 新增 Step 4.5 调用 updater（仅全自动模式）

### Task 5：创建 feedback.py 模块（Phase 3a）
- [ ] 5.1 实现 `detect_user_edits()` — 比对 front-matter created vs 文件 mtime
- [ ] 5.2 实现 `extract_feedback_signals()` — 分析编辑 diff 提取信号
- [ ] 5.3 实现 `get_feedback_context()` — 生成 prompt 注入文本
- [ ] 5.4 编写 `tests/test_feedback.py` 单元测试

### Task 6：集成反馈到 Agent（Phase 3a）
- [ ] 6.1 `pipeline.py` Step 2 扫描时检测用户编辑并生成 feedback_context
- [ ] 6.2 `auto_link_node` prompt 中注入 feedback_context
- [ ] 6.3 `AgentState` 新增 `feedback_context` 字段

### Task 7：质量评分体系（Phase 3b）
- [ ] 7.1 新增 `quality_score` 列到 DB（可选，或仅使用 front-matter）
- [ ] 7.2 实现 `compute_quality_score()` — 计算多维评分
- [ ] 7.3 `pipeline.py` 写入笔记时附带 quality front-matter
- [ ] 7.4 Agent `auto_link_node` 按 quality_score 排序 existing_notes
- [ ] 7.5 Refiner 双链补充优先推荐高质量笔记

## 六、文件变更清单

| 操作 | 文件 | 说明 |
|:---|:---|:---|
| **新增** | `vidbrain/updater.py` | 笔记增量更新器 |
| **新增** | `vidbrain/feedback.py` | 用户反馈检测与提取 |
| **新增** | `vidbrain/tests/test_updater.py` | updater 单元测试 |
| **新增** | `vidbrain/tests/test_feedback.py` | feedback 单元测试 |
| **修改** | `vidbrain/agent_graph.py` | 新增 suggest_update_node + State 扩展 + feedback_context |
| **修改** | `vidbrain/pipeline.py` | 扫描改用 rglob + 过滤 drafts + 集成 updater + 反馈检测 |
| **修改** | `vidbrain/db.py` | 新增 quality_score 列（可选） |
| **修改** | `vidbrain/refiner.py` | 双链推荐利用 quality_score |

## 七、执行顺序与依赖

```
Task 1 (F1+F2) ── 独立，无依赖，先做
     │
     ▼
Task 2 (updater.py) ── 依赖 Task 1
     │
     ▼
Task 3 (Agent 扩展) ── 依赖 Task 2
     │
     ▼
Task 4 (管线集成) ── 依赖 Task 3
     │
     ├──► Task 5 (feedback.py) ── 与 Task 3/4 可并行
     │         │
     │         ▼
     │    Task 6 (反馈集成) ── 依赖 Task 5 + Task 4
     │
     └──► Task 7 (质量评分) ── 依赖 Task 4
```

## 八、验证步骤

1. 语法检查：`uv run python -m py_compile` 验证所有修改文件
2. 单元测试：`uv run pytest vidbrain/tests/ -v` 全部通过
3. 冒烟测试：用 `--once` 模式处理 1-2 个测试视频，验证：
   - updater 能检测到相关笔记并生成更新建议
   - 更新安全写入（不破坏原内容）
   - feedback 能检测用户编辑
   - 质量评分正确写入 front-matter

## 九、决定性假设

1. **API 费用可控**：每处理一个视频，可能额外调用 1-3 次 LLM（一次关联检查 + 一次更新生成），仍在预算内
2. **更新只追加不删除**：保持数据安全性，所有修改均为增量追加
3. **用户反馈仅基于文件时间戳**：不解析 Obsidian 内部格式或依赖 Obsidian 插件
4. **DeepSeek V4 Flash 能力足够**：语义关联判断和更新建议生成当前模型能胜任
