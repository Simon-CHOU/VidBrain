# Spec: 本地视频 ASR 提取与云端 Agent 自迭代 Obsidian 知识库系统

## 1. 系统概述 (System Overview)

本系统旨在实现本地磁盘目录中 `.mp4` 技术视频资源的自动化 ASR 语音识别，并利用低成本的云端大模型（DeepSeek V4/Flash 等 API）驱动 LangGraph Agent 工作流，进行文本纠错、结构化知识提炼与 Obsidian 双链织网，最终全自动构建本地的“第二大脑”技术知识库。

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

## 5. 核心管道骨干代码实现

```python
import os
import json
import sqlite3
import hashlib
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from faster_whisper import WhisperModel
from openai import OpenAI
from langgraph.graph import StateGraph, END
from typing import TypedDict, List, Dict

# --- 配置区 ---
INPUT_DIR = "./input_videos"
ARCHIVE_DIR = "./archive_videos"
OBSIDIAN_VAULT_DIR = "/path/to/your/obsidian/vault"  # 替换为你的真实知识库路径
DB_PATH = "./pipeline.db"

DEEPSEEK_API_KEY = "your-deepseek-api-key"
DEEPSEEK_BASE_URL = "[https://api.deepseek.com/v1](https://api.deepseek.com/v1)"  # 或者是第三方聚合商的 base_url

client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

# --- 1. SQLite 基础设施 ---
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS video_pipeline (
            id TEXT PRIMARY KEY, video_name TEXT, file_path TEXT, status TEXT DEFAULT 'PENDING',
            raw_asr_json TEXT, error_message TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.commit()

def update_status(video_id, status, raw_asr=None, error_msg=None):
    with sqlite3.connect(DB_PATH) as conn:
        if raw_asr:
            conn.execute("UPDATE video_pipeline SET status=?, raw_asr_json=? WHERE id=?", (status, raw_asr, video_id))
        elif error_msg:
            conn.execute("UPDATE video_pipeline SET status=?, error_message=? WHERE id=?", (status, error_msg, video_id))
        else:
            conn.execute("UPDATE video_pipeline SET status=? WHERE id=?", (status, video_id))
        conn.commit()

# --- 2. 本地 CPU 密集型 ASR 引擎 ---
def run_local_asr(file_path):
    print(f"🎬 [ASR] 启动本地 CPU 密集型 ASR 引擎 (int8 量化): {file_path}")
    # 通过设置 cpu_threads 充分利用多核 CPU，建议设为逻辑核心数减 1 或 2
    model = WhisperModel("large-v3", device="cpu", compute_type="int8", cpu_threads=4)
    segments, info = model.transcribe(file_path, beam_size=5, vad_filter=True)
    
    asr_results = []
    for segment in segments:
        asr_results.append({
            "start": round(segment.start, 2),
            "end": round(segment.end, 2),
            "text": segment.text
        })
    return asr_results

# --- 3. LangGraph Agent 状态与节点 ---
class AgentState(TypedDict):
    video_id: str
    video_name: str
    raw_text: str
    existing_notes: List[str]
    final_markdown: str

def clean_and_extract_node(state: AgentState) -> Dict:
    print(f"🤖 [Agent: 清洗与提炼] 正在调用云端 DeepSeek 处理文本...")
    prompt = f"""你是一个资深的 AI Infrastructure 技术文档专家。请对以下技术视频的原始 ASR 文本进行处理：
1. 修正错别字，尤其是专业技术术语（例如将‘扣打’修正为‘CUDA’，‘卡夫卡’修正为‘Kafka’，‘单子融合’修正为‘算子融合’）。
2. 将无标点的文本根据语义进行结构化段落划分。
3. 提炼出核心原理、架构设计或核心代码/逻辑片段。

原始 ASR 文本：
{state['raw_text']}"""
    
    response = client.chat.completions.create(
        model="deepseek-v4-flash",  # 请根据实际模型名称填入
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2
    )
    return {"final_markdown": response.choices[0].message.content}

def auto_link_node(state: AgentState) -> Dict:
    print(f"🤖 [Agent: 自动织网] 正在结合本地知识库目录生成双链引流...")
    prompt = f"""你是一个高级知识库架构师。请在不破坏原有 Markdown 结构的前提下，比对给定的‘已有笔记列表’。
如果当前技术笔记中出现了列表中已有的概念，请自动将其转换为 Obsidian 双链语法 `[[已存在的笔记]]`。
如果发现非常关键、且列表里没有的全新技术名词，也请用 `[[新概念]]` 进行前瞻性标记。

已有笔记列表：{state['existing_notes']}

当前笔记内容：
{state['final_markdown']}"""

    response = client.chat.completions.create(
        model="deepseek-v4-flash",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1
    )
    return {"final_markdown": response.choices[0].message.content}

# --- 4. 组装 LangGraph 拓扑图 ---
def build_agent_graph():
    workflow = StateGraph(AgentState)
    workflow.add_node("clean_and_extract", clean_and_extract_node)
    workflow.add_node("auto_link", auto_link_node)
    
    workflow.set_entry_point("clean_and_extract")
    workflow.add_edge("clean_and_extract", "auto_link")
    workflow.add_edge("auto_link", END)
    return workflow.compile()

# --- 5. 管道流水线核心调度 ---
def process_pipeline(video_id, video_name, file_path):
    try:
        # Step 1: 本地 ASR
        update_status(video_id, "ASR_PROCESSING")
        asr_data = run_local_asr(file_path)
        raw_text = "\n".join([item['text'] for item in asr_data])
        update_status(video_id, "ASR_DONE", raw_asr=json.dumps(asr_data))
        
        # Step 2: 扫描本地知识库建立动态 Context
        update_status(video_id, "AGENT_PROCESSING")
        existing_notes = [p.stem for p in Path(OBSIDIAN_VAULT_DIR).glob("*.md")]
        
        # Step 3: 运行 Agent
        graph = build_agent_graph()
        initial_state = {
            "video_id": video_id,
            "video_name": video_name,
            "raw_text": raw_text,
            "existing_notes": existing_notes,
            "final_markdown": ""
        }
        final_state = graph.invoke(initial_state)
        
        # Step 4: 刷入 Obsidian 磁盘
        output_file_name = f"{Path(video_name).stem}.md"
        output_path = os.path.join(OBSIDIAN_VAULT_DIR, output_file_name)
        
        front_matter = f"---\ntype: technical-note\nsource_video: {video_name}\nstatus: auto-generated\n---\n\n"
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(front_matter + final_state["final_markdown"])
            
        # Step 5: 安全移出输入目录（归档）
        update_status(video_id, "SUCCESS")
        os.makedirs(ARCHIVE_DIR, exist_ok=True)
        archive_path = os.path.join(ARCHIVE_DIR, video_name)
        
        # 处理可能存在的同名归档文件冲突
        if os.path.exists(archive_path):
            os.remove(archive_path)
        os.rename(file_path, archive_path)
        print(f"🎉 [SUCCESS] 链路完整闭环。知识文件已生成: {output_path}")
        
    except Exception as e:
        print(f"❌ [FAILED] 管道运行崩溃: {str(e)}")
        update_status(video_id, "FAILED", error_msg=str(e))

# --- 6. 本地文件系统 Watchdog 事件处理器 ---
class VideoFileHandler(FileSystemEventHandler):
    def on_closed(self, event):
        # 确保只捕捉完全写入并关闭句柄的 .mp4 文件
        if not event.is_directory and event.src_path.endswith(".mp4"):
            video_name = os.path.basename(event.src_path)
            # 使用文件名生成确定性的唯一 ID
            video_id = hashlib.md5(video_name.encode('utf-8')).hexdigest()
            print(f"📥 [Watchdog] 检测到本地新视频完全落盘: {video_name}")
            
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("INSERT OR IGNORE INTO video_pipeline (id, video_name, file_path) VALUES (?, ?, ?)",
                             (video_id, video_name, event.src_path))
                conn.commit()
            
            # 同步单线程串行处理，避免本地 CPU 被多个同时跑的 ASR 撑爆
            process_pipeline(video_id, video_name, event.src_path)

if __name__ == "__main__":
    init_db()
    os.makedirs(INPUT_DIR, exist_ok=True)
    
    event_handler = VideoFileHandler()
    observer = Observer()
    observer.schedule(event_handler, path=INPUT_DIR, recursive=False)
    observer.start()
    print(f"🚀 磁盘监听已启动。请将任何技术视频剪切/移动到目录: {INPUT_DIR}")
    
    try:
        while True:
            import time
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

```
