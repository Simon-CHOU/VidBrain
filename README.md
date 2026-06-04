# VidBrain

本地视频 ASR 提取与云端 Agent 自迭代 Obsidian 知识库系统。

自动监听本地目录中的 `.mp4` 技术视频，通过 faster-whisper（CPU int8 量化）进行语音识别，再调用 DeepSeek API 驱动 LangGraph Agent 工作流进行文本纠错、知识提炼和 Obsidian 双链织网，最终全自动构建本地的技术知识库。

## 环境要求

- Python 3.10+
- [uv](https://docs.astral.sh/uv/)（推荐）或 pip
- 约 3GB 磁盘空间用于 faster-whisper large-v3 模型

## 快速开始

### 1. 克隆项目并创建虚拟环境

```bash
cd VidBrain
uv venv .venv
.venv\Scripts\activate
```

### 2. 安装依赖

```bash
uv sync
```

### 3. 配置环境变量

在 Windows 系统中设置以下**系统环境变量**（LLM 凭据，不会写入代码库）：

| 变量名 | 说明 |
|--------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 |
| `DEEPSEEK_BASE_URL` | DeepSeek API 基础地址（例如 `https://api.deepseek.com/v1`） |

设置方式：Windows 设置 → 系统 → 关于 → 高级系统设置 → 环境变量 → 新建系统变量。

### 4. 运行

```bash
# 持续监听模式
python -m vidbrain.main --vault-dir D:\path\to\obsidian\vault

# 处理现有视频后退出
python -m vidbrain.main --vault-dir D:\path\to\obsidian\vault --once
```

### 全部 CLI 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--input-dir` | `I:\web-videos` | 输入目录（存放 .mp4 网络视频，递归扫描子目录） |
| `--vault-dir` | **必填** | Obsidian Vault 路径 |
| `--db-path` | `./pipeline.db` | SQLite 数据库路径 |
| `--model-size` | `large-v3` | Whisper 模型大小 |
| `--cpu-threads` | 自动检测 | ASR 使用的 CPU 线程数 |
| `--once` | false | 处理现有文件后退出（不持续监听） |

## 项目结构

```
VidBrain/
├── vidbrain/           # 主包
│   ├── config.py      # 配置管理
│   ├── logger.py      # 日志（自动脱敏）
│   ├── db.py          # SQLite 数据库
│   ├── asr_engine.py  # faster-whisper 引擎
│   ├── agent_graph.py # LangGraph 工作流
│   ├── pipeline.py    # 管线调度
│   ├── watcher.py     # 文件监听
│   └── main.py        # 主入口
├── logs/              # 日志文件
└── pipeline.db        # SQLite 数据库
```

## 重要约束

- **永远不得修改输入目录下的任何文件**：VidBrain 以只读方式访问 `I:\web-videos` 目录。程序不会对该目录下的文件进行任何创建、修改、重命名或删除操作。
- 处理完成的视频不会被移动或删除，仅通过 SQLite 数据库标记状态（`SUCCESS`），避免重复处理。
- 唯一输出位置是 Obsidian Vault，即 `--vault-dir` 指向的目录。

## 安全说明

- `DEEPSEEK_API_KEY` 和 `DEEPSEEK_BASE_URL` 从 Windows 系统环境变量读取
- 不会写入任何文件、代码库或日志
- 日志系统自动脱敏所有敏感字段（key / secret / token / sk）
- 请勿将 API Key 以任何形式提交到版本控制
