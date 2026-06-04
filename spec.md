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
🎙️ 2. Local ASR Engine (本地 CPU 密集型任务: Faster-Whisper int8 量化)
│
▼ (生成带时间戳的原始 JSON，持久化至 SQLite)
💾 3. Local SQLite DB (任务状态机 & ASR 缓存缓存)
│
▼ (触发 LangGraph 工作流，分块读取 ASR 文本)
🤖 4. LangGraph Agent (云端轻量调度) ─── API 调用 (sk + base_url) ───► [DeepSeek V4/Flash API]
│                                                                  │
├─► 节点 A: clean_node (术语纠错与分段) ◄───────────────────────────┘
├─► 节点 B: extract_node (核心知识提炼) ◄──────────────────────────┘
└─► 节点 C: link_node (扫描 Obsidian Vault 目录动态双链织网) ◄──────┘
│
├──► 📂 5a. 最终输出：Obsidian Vault (纯 Markdown 文件群，包含双链)
└──► 📂 5b. 文件归档：本地归档目录 (./archive_videos/*.mp4)

```

### 2.2 数据流向说明
1. **文件检测**：当任何外部程序或用户将 `.mp4` 视频放入本地 `input_dir/` 且写入完成后，`watchdog` 捕捉到关闭句柄事件，在 SQLite 中初始化一条 `PENDING` 任务记录。
2. **本地 ASR 阶段**：后台管线轮询 SQLite，调用本地 CPU 密集型 ASR 引擎，解析出带精确时间戳的文本，将 JSON 灌入数据库，并将状态更新为 `ASR_DONE`。
3. **云端 Agent 阶段**：LangGraph 框架启动。工作流先读取当前 Obsidian 目录下的所有 `.md` 文件名建立索引视图。随后将 ASR 文本送入 DeepSeek API 进行术语纠错、知识块提炼（概念、代码、踩坑点）。最后利用索引视图，让大模型自动在文本中编织 `[[双链]]`。
4. **归档流**：知识笔记原子化写入 Obsidian Vault。SQLite 任务状态置为 `SUCCESS`。原始 `.mp4` 文件从 `input_dir/` 剪切移至 `archive_dir/`，防止二次触发。

---

## 3. 技术选型 (Technology Stack)

| 模块 | 技术选型 | 原因与优势 |
| :--- | :--- | :--- |
| **输入触发** | `watchdog` (Python) | 纯本地文件系统事件驱动，精准捕获文件落盘。 |
| **ASR 引擎** | `faster-whisper` (CTranslate2) | 极致的 CPU 优化。通过 `int8` 量化在纯 CPU 跑 `large-v3` 依然极具吞吐量优势。 |
| **临时缓存/状态机**| **SQLite 3** | 单文件免运维，完美承载本地 Pipeline 的状态流转与断点续传。 |
| **Agent 框架** | **LangGraph** | 基于有向图（DAG）的命令式状态机，对于有确定性先后顺序的 AI 工作流最可控。 |
| **大模型驱动** | **DeepSeek V4 / Flash API** | 百万 Token 极其廉价，超大上下文，极强指令遵循与结构化生成能力。 |
| **持久化存储** | **Obsidian Vault** | 本地纯文本，利用文件目录作为天然的关系索引。 |

---

## 4. 数据库设计 (SQLite Schema)

```sql
CREATE TABLE IF NOT EXISTS video_pipeline (
    id TEXT PRIMARY KEY,               -- 视频文件名的 MD5 或唯一哈希
    video_name TEXT NOT NULL,          -- 原始文件名 (如: operator_fusion_cuda.mp4)
    file_path TEXT NOT NULL,           -- 本地当前绝对路径
    status TEXT DEFAULT 'PENDING',     -- PENDING, ASR_PROCESSING, ASR_DONE, AGENT_PROCESSING, SUCCESS, FAILED
    raw_asr_json TEXT,                 -- 存储带时间戳的原始 ASR 结果 (JSON 字符串)
    error_message TEXT,                -- 报错信息排查
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

```

---

## 5. 实现代码

实际实现代码位于 `vidbrain/` 包中，模块化组织如下：

| 模块 | 文件 | 职责 |
|:---|:---|:---|
| **配置** | `vidbrain/config.py` | 从 CLI 参数和 Windows 系统环境变量（`DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL`）读取配置 |
| **日志** | `vidbrain/logger.py` | 统一日志输出，自动脱敏敏感信息（API Key 等） |
| **数据库** | `vidbrain/db.py` | SQLite 封装，线程安全，任务状态机管理 |
| **ASR 引擎** | `vidbrain/asr_engine.py` | faster-whisper 全局单例封装（CPU int8 量化） |
| **Agent 工作流** | `vidbrain/agent_graph.py` | LangGraph 定义：文本纠错/提炼 → 双链织网 |
| **管线调度** | `vidbrain/pipeline.py` | 核心 Pipeline：ASR → Agent → 写入 Vault → 归档 |
| **文件监听** | `vidbrain/watcher.py` | watchdog 事件监听 + 线程池异步处理 |
| **主入口** | `vidbrain/main.py` | CLI 参数解析 + 各模块组装 + 启动 |
