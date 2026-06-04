# VidBrain 实施计划

## 一、方案审查结论

### 现状
- 当前项目仅有一份 `spec.md` 设计文档，**没有任何实际代码或项目结构**。
- spec 文档中包含一段完整的 Python 代码草稿（约 275 行），嵌入在 Markdown 代码块中，含有占位 API Key 和路径。

### Spec 方案优点
1. 架构清晰：本地 ASR（CPU 密集型）+ 云端 LLM（Token 密集型）的混合架构合理。
2. 技术选型成熟：watchdog/faster-whisper/SQLite/LangGraph/DeepSeek 均为经过验证的技术。
3. 数据流闭环完整：从文件检测→ASR→Agent 处理→知识库输出→归档，链路清晰。

### Spec 中需要改进的问题
| # | 问题 | 说明 |
|---|------|------|
| 1 | **单体脚本** | 所有代码在一个函数集合中，缺乏模块化 |
| 2 | **SK 暴露风险** | API Key 硬编码在代码中，容易泄露到版本控制 |
| 3 | **模型反复加载** | `run_local_asr()` 每次调用都重新加载 WhisperModel（~3GB），应设为全局单例 |
| 4 | **同步阻塞 Watchdog** | `on_closed` 中直接调用 `process_pipeline`，ASR 运行时阻塞文件监听 |
| 5 | **无日志系统** | 仅用 print，无法定位生产问题 |
| 6 | **无重试机制** | API 调用失败、文件冲突等无重试逻辑 |
| 7 | **依赖未管理** | 无 pyproject.toml，无 venv |
| 8 | **状态管理简化** | `updated_at` 字段从未更新 |

---

## 二、项目结构

```
VidBrain/
├── .gitignore                  # 忽略 venv/、__pycache__/、*.db、input/、archive/、logs/
├── pyproject.toml              # uv 项目管理（依赖声明 + 项目元数据）
├── README.md                   # 使用说明
│
├── vidbrain/                   # 主包
│   ├── __init__.py
│   ├── config.py               # 配置管理（LLM 从系统环境变量读取，其他从 CLI 参数）
│   ├── db.py                   # SQLite 数据库操作
│   ├── asr_engine.py           # faster-whisper 封装（模型单例）
│   ├── agent_graph.py          # LangGraph 工作流定义
│   ├── pipeline.py             # 核心管线调度逻辑
│   ├── watcher.py              # watchdog 文件监听
│   ├── logger.py               # 日志配置
│   └── main.py                 # 入口：CLI 参数解析 + 启动
│
├── input_videos/               # 输入目录（自动创建，已 gitignore）
├── archive_videos/             # 归档目录（自动创建，已 gitignore）
├── logs/                       # 日志目录（自动创建，已 gitignore）
└── pipeline.db                 # SQLite 数据库（自动创建，已 gitignore）
```

---

## 三、分步实施任务

### 阶段 0：先更新文档，再写代码

> 根据要求，先更新 `.md` 等文档，再执行代码变更。

**步骤 0.1**：更新 `spec.md`
- 删除 spec.md 中嵌入的 Python 草稿代码（275 行的大代码块）
- 原因：该代码含占位 API Key、路径硬编码、且已是过时的单体脚本设计
- 替换为指向实际源码的简短引用说明

**步骤 0.2**：编写 `README.md`
- 项目简介
- 环境要求（Python 3.10+）
- 快速开始（venv + uv 安装 + 运行）
- 环境变量配置说明（DEEPSEEK_BASE_URL、DEEPSEEK_API_KEY）

**步骤 0.3**：编写 `.gitignore`
- 忽略：`.venv/`、`__pycache__/`、`*.db`、`input_videos/`、`archive_videos/`、`logs/`、`.env`（如有）
- 确保 `.gitignore` 覆盖所有不应进入版本控制的目录和文件

---

### 任务 1：项目基础设施

**目标**：创建项目骨架、依赖管理

**包管理说明**：
- 使用 **`uv`** 管理包和虚拟环境
- 使用 **`venv`** 创建 Python 虚拟环境（`.venv/` 目录）
- 声明依赖在 `pyproject.toml` 中，而不是 `requirements.txt`

**文件**：`pyproject.toml`

```toml
[project]
name = "vidbrain"
version = "0.1.0"
description = "本地视频 ASR 提取与云端 Agent 自迭代 Obsidian 知识库系统"
requires-python = ">=3.10"
dependencies = [
    "faster-whisper>=1.1.0",
    "watchdog>=6.0.0",
    "openai>=1.55.0",
    "langgraph>=0.2.0",
]
```

**文件**：`vidbrain/__init__.py` — 空包文件

**文件**：`vidbrain/config.py` — 配置管理

**LLM 配置安全规则（重要）**：
- `DEEPSEEK_API_KEY` 和 `DEEPSEEK_BASE_URL` 从 **Windows 系统环境变量** 读取（`os.environ`）
- **不写入任何文件**（不在 `.env`、不在日志、不在源码中）
- **日志中不得输出** API Key 的任何部分
- `deepseek-v4-flash` 作为模型名在代码中硬编码（无需用户配置）
- LLM 配置不可通过 CLI 参数覆盖（防止意外泄露）

其他配置通过 CLI 参数传入（Python `argparse`）：
- `--input-dir`（默认 `./input_videos`）
- `--archive-dir`（默认 `./archive_videos`）
- `--vault-dir`（Obsidian Vault 路径，必填）
- `--db-path`（默认 `./pipeline.db`）
- `--model-size`（Whisper 模型大小，默认 `large-v3`）
- `--cpu-threads`（默认自动检测逻辑核心数）
- `--once`（处理现有文件后退出，不持续监听）

---

### 任务 2：日志系统

**目标**：统一日志输出，确保不泄露敏感信息

**文件**：`vidbrain/logger.py`

- 使用 Python `logging` 模块
- 控制台输出：INFO 级别
- 文件输出：DEBUG 级别，写入 `logs/vidbrain.log`
- 日志轮转：按大小（10MB）轮转，保留 3 个备份
- **禁止日志过滤规则**：在日志格式化器或过滤器中对任何包含 `key`、`secret`、`token`、`sk` 字段的内容进行脱敏处理（`***MASKED***`）

---

### 任务 3：数据库层

**目标**：封装 SQLite 操作，支持线程安全访问

**文件**：`vidbrain/db.py`

- 实现 `DatabaseManager` 类：
  - `init_db()` — 建表（包含 `updated_at` 通过触发器自动更新）
  - `create_task(video_id, video_name, file_path)` — 插入新任务
  - `update_status(video_id, status, raw_asr=None, error_msg=None)` — 更新状态
  - `get_pending_tasks()` — 获取待处理任务列表
  - `get_task(video_id)` — 查询单个任务
- 使用 `threading.Lock` 确保 SQLite 线程安全

---

### 任务 4：ASR 引擎封装

**目标**：将 faster-whisper 封装为全局单例，避免重复加载模型

**文件**：`vidbrain/asr_engine.py`

- 实现 `ASREngine` 类：
  - 模块级全局单例：`_model = None`，延迟初始化，仅在首次调用时加载
  - `transcribe(file_path) -> List[Dict]` — 执行转录，返回带时间戳的 segments
  - 参数：`model_size`、`device="cpu"`、`compute_type="int8"`、`cpu_threads` 从配置读取
  - `vad_filter=True` 过滤静音段

---

### 任务 5：LangGraph Agent 工作流

**目标**：实现基于 DeepSeek API 的知识处理流程

**文件**：`vidbrain/agent_graph.py`

- 定义 `AgentState` (TypedDict)：
  - `video_id`, `video_name`, `raw_text`, `existing_notes`, `final_markdown`

- **节点 1：`clean_and_extract_node`** — 术语纠错 + 分段 + 提炼核心知识
  - 调用 deepseek-v4-flash，temperature=0.2
  - 提示词中要求：修正技术术语、结构化段落、提炼核心原理/代码

- **节点 2：`auto_link_node`** — 基于 Obsidian Vault 已有笔记生成 `[[双链]]`
  - 调用 deepseek-v4-flash，temperature=0.1
  - 对比已有笔记列表，自动生成双链

- **重试机制**：
  - API 调用失败时最多重试 3 次
  - 指数退避（1s → 2s → 4s）

- **安全措施**：
  - Agent 调用中任何日志/异常消息均需脱敏
  - `openai.Client` 初始化时不记录 API Key 到日志
  - API 响应中若返回 API Key 相关信息，不写入日志

---

### 任务 6：管线调度逻辑

**目标**：实现核心 Pipeline 调度，连接所有模块

**文件**：`vidbrain/pipeline.py`

- `process_pipeline(video_id, video_name, file_path)`：
  1. 更新状态 `ASR_PROCESSING`
  2. 调用 ASREngine 转录
  3. 原始结果（JSON）存入 SQLite，状态置为 `ASR_DONE`
  4. 扫描 Obsidian Vault 获取已有笔记列表（`*.md` 文件名）
  5. 拼接 ASR 文本，调用 Agent 工作流
  6. 写 Markdown 文件到 Obsidian Vault（含 front-matter）
  7. 状态置为 `SUCCESS`
  8. 移动视频文件到 `archive_videos/`（若已存在则追加时间戳）

---

### 任务 7：文件监听与异步调度

**目标**：实现 watchdog 监听 + 线程池异步处理，不阻塞监听器

**文件**：`vidbrain/watcher.py`

- `VideoHandler(FileSystemEventHandler)`：
  - `on_closed(event)` — 仅处理 `.mp4` 文件
  - 插入任务到 SQLite（`INSERT OR IGNORE`）
  - 提交到线程池异步执行，不阻塞监听

- 使用 `ThreadPoolExecutor(max_workers=1)`：
  - ASR 是 CPU 密集型，同时跑多个会导致 OOM

---

### 任务 8：主入口

**文件**：`vidbrain/main.py`

- 使用 `argparse` 解析 CLI 参数
- 初始化：日志 → 数据库 → ASR 引擎（预加载）
- 启动 watchdog 监听
- `--once` 模式：处理现有文件后优雅退出

---

## 四、关键设计决策

| 决策项 | 选择 | 理由 |
|--------|------|------|
| **包管理** | `uv` + `venv` | 用户指定，uv 速度更快，venv 隔离环境 |
| **LLM 凭据** | Windows 系统环境变量 `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` | 用户指定，避免 SK 暴露到版本控制和日志 |
| **LLM 模型** | 硬编码 `deepseek-v4-flash` | 用户指定，无需用户额外配置 |
| **ASR 模型加载** | 模块级全局单例 | 避免每次处理视频都加载 ~3GB 模型 |
| **异步调度** | `ThreadPoolExecutor` (max_workers=1) | ASR 是 CPU 密集型，同时跑多个会导致 OOM |
| **Agent 重试** | 自定义装饰器，指数退避 (1s/2s/4s) | API 调用可能因限流/网络波动失败 |
| **文件冲突** | 归档时追加时间戳 | 避免同名文件覆盖 |
| **SK 安全** | os.environ 读取；日志脱敏过滤；不写入任何文件 | 防止 SK 泄露到代码库、日志、备份 |

---

## 五、执行顺序

```
Step 0 → 更新文档（spec.md 删代码、写 README.md、写 .gitignore）
Step 1 → 项目骨架（pyproject.toml、__init__.py、config.py）
Step 2 → 日志系统（logger.py）
Step 3 → 数据库层（db.py）
Step 4 → ASR 引擎（asr_engine.py）
Step 5 → Agent 工作流（agent_graph.py）
Step 6 → 管线调度（pipeline.py）
Step 7 → 文件监听（watcher.py）
Step 8 → 主入口（main.py）
```

每个步骤实现后执行 `uv run python -m py_compile vidbrain/<file>.py` 验证语法正确性。

---

## 六、验证步骤

1. **语法检查**：`uv run python -m py_compile vidbrain/*.py`
2. **空运行**：`uv run python -m vidbrain.main --once`（无 --vault-dir 时输出提示，验证 CLI 解析正常）
3. **功能验证**：放入一个短视频到 `input_videos/`，运行主程序，检查：
   - SQLite 中状态是否正确流转
   - Obsidian Vault 中是否生成 `.md` 文件
   - 视频是否成功归档
   - `logs/vidbrain.log` 中**不包含** API Key
