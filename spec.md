# Spec: 本地视频 ASR 提取与云端 Agent 自迭代 Obsidian 知识库系统

## 1. 系统概述 (System Overview)

本系统旨在实现本地磁盘目录中 `.mp4` 技术视频资源的自动化 ASR 语音识别，并利用低成本的云端大模型（DeepSeek V4/Flash 等 API）驱动 LangGraph Agent 工作流，进行文本纠错、结构化知识提炼与 Obsidian 双链织网，最终全自动构建本地的"第二大脑"技术知识库。

系统输入完全基于**本地文件系统事件触发**，采用**本地 ASR 计算（CPU 密集型） + 云端 LLM 推理（Token 密集型）**的混合架构。

---

## 2. 架构设计 (Architecture Design)

### 2.1 核心拓扑图

```

📂 1. 本地输入目录 (./input_videos/*.mp4)
│
▼ (Python Watchdog 异步监听文件写入完成事件: on_closed)
🎙️ 2. Local ASR Engine (本地 CPU 密集型任务: Faster-Whisper int8 量化 / Vulkan whisper.cpp)
│
▼ (生成带时间戳的原始 JSON，持久化至 SQLite)
💾 3. Local SQLite DB (任务状态机 & ASR 缓存缓存)
│
▼ (触发 LangGraph 工作流，分块读取 ASR 文本)
🤖 4. LangGraph Agent (云端轻量调度) ─── API 调用 (sk + base_url) ───► [DeepSeek V4/Flash API]
│                                                                  │
├─► 节点 A: clean_and_extract_node (术语纠错与分段) ◄─────────────────┘
└─► 节点 B: auto_link_node (扫描 Obsidian Vault 目录动态双链织网) ◄────┘
│
├──► 📂 5a. 最终输出：Obsidian Vault (纯 Markdown 文件群，包含双链)
│
└──► 📂 5b. 增量更新 (check_and_update): 检测关联笔记并自动补充引用

```

### 2.2 数据流向说明
1. **文件检测**：当任何外部程序或用户将 `.mp4` 视频放入本地 `input_dir/` 且写入完成后，`watchdog` 捕捉到关闭句柄事件，在 SQLite 中初始化一条 `PENDING` 任务记录。
2. **分类**：通过文件名关键词匹配将视频分为 `tech`（技术类）/ `skip`（非技术类）/ `unclear`（待审核）。
3. **本地 ASR 阶段**：后台管线轮询 SQLite，调用本地 CPU 密集型 ASR 引擎，解析出带精确时间戳的文本，将 JSON 灌入数据库，并将状态更新为 `ASR_DONE`。
4. **云端 Agent 阶段**：LangGraph 框架启动。工作流先读取当前 Obsidian 目录下的所有 `.md` 文件名建立索引视图。随后将 ASR 文本送入 DeepSeek API 进行术语纠错、知识块提炼（概念、代码、踩坑点）。最后利用索引视图，让大模型自动在文本中编织 `[[双链]]`。
5. **增量更新**：Agent 完成后，`check_and_update()` 检测新笔记与已有笔记的关联，通过 LLM 生成更新建议并自动应用到关联笔记。
6. **产出流**：知识笔记原子化写入 Obsidian Vault。SQLite 任务状态置为 `SUCCESS`。原始 `.mp4` 文件保留在原位，不会被移动或删除（只读约束）。

---

## 3. 技术选型 (Technology Stack)

| 模块 | 技术选型 | 原因与优势 |
| :--- | :--- | :--- |
| **输入触发** | `watchdog` (Python) | 纯本地文件系统事件驱动，精准捕获文件落盘。 |
| **ASR 引擎** | `faster-whisper` (CTranslate2) / whisper.cpp Vulkan | CPU: int8 量化极致优化；Vulkan: GPU 加速备选方案。 |
| **临时缓存/状态机**| **SQLite 3 (WAL 模式)** | 单文件免运维，WAL 写入不阻塞读取，完美承载本地 Pipeline 的状态流转与断点续传。 |
| **Agent 框架** | **LangGraph** | 基于有向图（DAG）的命令式状态机，编译结果可缓存复用。 |
| **大模型驱动** | **DeepSeek V4 / Flash API** | 百万 Token 极其廉价，超大上下文，极强指令遵循与结构化生成能力。 |
| **Embedding (可选)** | **DashScope text-embedding-v4** | 用于语义检索和 Map-of-Content 聚类精炼。 |
| **持久化存储** | **Obsidian Vault** | 本地纯文本，利用文件目录作为天然的关系索引。 |
| **性能管理** | **PerformanceProfile** | 自动检测桌面空闲状态，动态切换满负荷/省电模式。 |

---

## 4. 数据库设计 (SQLite Schema)

```sql
CREATE TABLE IF NOT EXISTS video_pipeline (
    id TEXT PRIMARY KEY,               -- 视频文件名的 MD5 或唯一哈希
    video_name TEXT NOT NULL,          -- 原始文件名 (如: operator_fusion_cuda.mp4)
    file_path TEXT NOT NULL,           -- 本地当前绝对路径
    status TEXT DEFAULT 'PENDING',     -- PENDING → ASR_PROCESSING → ASR_DONE → AGENT_PROCESSING → AGENT_DONE → SUCCESS
                                       -- 或 PENDING → DRAFT_PENDING (半自动模式)
                                       -- 失败: FAILED / PERMANENTLY_FAILED (重试 3 次后)
    category TEXT,                     -- tech / skip / unclear (文件名分类结果)
    classify_reason TEXT,              -- 分类理由说明
    raw_asr_json TEXT,                 -- 存储带时间戳的原始 ASR 结果 + Agent final_markdown (JSON 字符串)
    error_message TEXT,                -- 报错信息排查
    retry_count INTEGER DEFAULT 0,     -- 重试计数
    last_error TEXT,                   -- 最后一次错误信息
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Metrics 快照表（定期落盘运行时指标）
CREATE TABLE IF NOT EXISTS metrics_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 审计日志表（双通道：DB + JSONL 文件）
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    component TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'success',
    video_id TEXT DEFAULT '',
    video_name TEXT DEFAULT '',
    details_json TEXT DEFAULT '{}',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### 状态机流转

```
PENDING → ASR_PROCESSING → ASR_DONE → AGENT_PROCESSING → AGENT_DONE → SUCCESS
                                                       ↘ DRAFT_PENDING (半自动)
PENDING → … (failure) → PENDING (retry 1-3) / PERMANENTLY_FAILED (retry ≥3)
ASR_PROCESSING / AGENT_PROCESSING → PENDING (启动恢复)
```

---

## 5. 实现代码

实际实现代码位于 `src/` 包中，模块化组织如下：

| 模块 | 文件 | 职责 |
|:---|:---|:---|
| **配置** | `src/models/config.py` | 从 CLI 参数和系统环境变量（`DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` / `DASHSCOPE_API_KEY`）读取配置；HF 缓存管理 |
| **状态** | `src/models/state.py` | `AgentState` TypedDict 定义（LangGraph 工作流状态） |
| **CLI** | `src/cli.py` | 命令行参数解析（与重量级依赖隔离，便于测试） |
| **日志** | `src/utils/logger.py` | 统一日志输出，自动脱敏敏感信息（API Key 等） |
| **数据库** | `src/utils/db.py` | SQLite 封装（WAL 模式），线程安全，任务状态机管理 |
| **ASR 引擎** | `src/services/asr_service.py` | faster-whisper 全局单例封装（CPU int8 量化），本地缓存优先 |
| **ASR Vulkan** | `src/services/asr_vulkan_service.py` | whisper.cpp Vulkan 后端，GPU 加速备选 |
| **Agent 工作流** | `src/services/agent_service.py` | LangGraph 定义：clean_and_extract_node → auto_link_node（编译结果缓存 + 共享 HTTP 客户端） |
| **管线调度** | `src/services/pipeline_service.py` | 核心 Pipeline：ASR → Agent → 写入 Vault → 增量更新 |
| **分类器** | `src/services/classifier_service.py` | 文件名关键词分类（tech/skip/unclear） |
| **更新服务** | `src/services/updater_service.py` | 检测关联笔记 → LLM 生成更新建议 → 应用更新 |
| **精炼服务** | `src/services/refiner_service.py` | Map-of-Content 聚类精炼 + 交叉引用优化 |
| **Embedding** | `src/services/embedding_service.py` | DashScope embedding API + numpy 余弦相似度 + k-means 聚类 |
| **反馈检测** | `src/services/feedback_service.py` | 检测用户编辑 + 提取反馈信号 + 生成反馈上下文 |
| **草稿管理** | `src/services/drafts_service.py` | 半自动模式草稿的读写与发布 |
| **文件监听** | `src/utils/watcher.py` | watchdog 事件监听 + 去抖 + 队列背压保护 + 异步提交 |
| **Vault 缓存** | `src/utils/vault_cache.py` | 笔记列表缓存（按 quality_score 排序），mtime 变更检测 |
| **资源调控** | `src/utils/throttle.py` | 进程/线程优先级 + PerformanceProfile 动态管理 |
| **Metrics** | `src/utils/metrics.py` | 运行时指标收集（p50/p95/p99），定期落盘 SQLite + JSON 导出 |
| **审计** | `src/utils/audit.py` | 双通道审计日志（JSON Lines + SQLite），全链路追踪 |
| **单例** | `src/utils/singleton.py` | 进程级单实例锁 |
| **主入口** | `src/main.py` | CLI 参数解析 + 各模块组装 + 持续/定时/流式三种运行模式 |

---

## 6. 运行模式

### 6.1 全自动模式（默认）
```bash
uv run python -m src.main --input-dir ./videos --vault-dir ./my_vault --interval 30m
```
每一轮：等待 30 分钟 → 扫描新文件 → 分类 → 处理一批（ASR → Agent → 写入 Vault）

### 6.2 流式持续模式
```bash
uv run python -m src.main --input-dir ./videos --vault-dir ./my_vault --continuous
```
处理完一个视频立即取下一个，不等待间隔。

### 6.3 半自动模式
```bash
uv run python -m src.main --input-dir ./videos --vault-dir ./my_vault --semi
```
人工审核门禁：分类审核 → 队列审批 → 处理 → 草稿审核。

### 6.4 性能 Profile
```bash
# 自动切换（桌面空闲 → 满负荷，活跃 → 省电）
--profile auto
# 固定满负荷
--profile idle
# 固定省电
--profile active
```

---

## 7. 可观测性

- **MetricsCollector**: 计数器 + 延迟百分位（p50/p95/p99）+ 快照定时落盘
- **AuditLogger**: 双通道（JSON Lines 文件 + SQLite 表），全链路事件追踪
- **优雅退出**: SIGINT/SIGTERM 信号处理，等待当前任务完成后 flush 指标和审计日志
