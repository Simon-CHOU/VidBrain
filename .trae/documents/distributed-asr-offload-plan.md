# 分布式 ASR 卸载方案研究计划

## 1. 摘要

桌面（AMD iGPU, Vulkan 无效）上的 ASR 推理卸载到局域网内笔记本（NVIDIA RTX 2060 6GB），实现无感热插拔：
- 笔记本离开 → 自动回退 CPU（faster-whisper）
- 笔记本归位 → 自动升级 GPU（远程 whisper.cpp）

输出文件：`./docs/distributed-asr-offload-research.md`

---

## 2. 现状分析

### 2.1 当前 ASR 架构（基于代码审查）

VidBrain 已有双后端 ASR 架构：

| 后端 | 文件 | 引擎 | 设备 |
|------|------|------|------|
| CPU | `vidbrain/asr_engine.py` | faster-whisper (CTranslate2) | CPU, int8 |
| GPU | `vidbrain/asr_engine_vulkan.py` | whisper.cpp CLI 子进程 | 本地 Vulkan |

关键发现：
- **无统一设备抽象层** — `main.py` 中用 `if/else` 分支选择后端，无策略模式/工厂/抽象基类
- **Vulkan 检测粗糙** — 仅检查 `whisper-cli --help` 退出码，不验证实际 GPU 能力
- **`PipelineConfig.asr_backend`** 是简单字符串字段，取值 `"cpu"` 或 `"vulkan"`
- **CPU 预下载作为安全网** — 即使 Vulkan 模式也会预下载 faster-whisper 模型
- **音频提取独立于 ASR 推理** — `_extract_audio()` 用 ffmpeg，本地执行

### 2.2 硬件约束

| | Desktop | Laptop |
|---|---------|--------|
| GPU | AMD iGPU (集成显卡) | NVIDIA RTX 2060 6GB |
| ASR 现状 | Vulkan 几乎无效，实际走 CPU | 闲置 GPU 算力 |
| 网络 | 同一局域网 | 同一局域网，可能被带出门 |

---

## 3. 研究问题

1. 用什么通信协议？HTTP / gRPC / SSH + SCP / 其他？
2. 服务发现机制：如何自动检测笔记本在/不在局域网？
3. 热插拔切换：如何在 ASR 后端之间无缝切换？
4. 数据传输效率：音频文件传输开销 vs GPU 推理加速收益
5. 容错与超时：笔记本突然断网时怎么处理？
6. 安全考量：局域网内暴露服务的基本安全

---

## 4. 方案候选

### 方案 A：轻量 HTTP 服务 + mDNS 发现

```
Desktop (Client)                    Laptop (Server)
┌─────────────────┐                ┌─────────────────────┐
│ asr_engine_     │   HTTP POST    │ whisper-server      │
│ remote.py       │ ──────────────>│ (FastAPI/Flask)     │
│                 │  audio.wav     │                     │
│ 或扩展现有      │ <──────────────│ whisper.cpp subprocess│
│ asr_engine_     │  JSON result   │ (CUDA backend)      │
│ vulkan.py       │                │                     │
│                 │                │ mDNS advertisement  │
│ Service Discover│ ── Bonjour ──> │ "_whisper._tcp"     │
└─────────────────┘                └─────────────────────┘
```

### 方案 B：SSH + SCP 远程执行

```
Desktop                              Laptop
ffmpeg → WAV → SCP upload ────────→ whisper.cpp 执行
              ← SCP download ─────── JSON result
```

### 方案 C：共享文件系统 + 任务轮询

```
Desktop 写入 audio.wav → 共享文件夹 → Laptop 轮询处理 → 写入 result.json
```

---

## 5. 推荐方案：方案 A（HTTP + mDNS）

理由：
1. **延迟最低** — 单次 HTTP round-trip vs SSH 多步交互
2. **热插拔最友好** — mDNS 自动注册/注销，无需轮询
3. **实现成本最低** — FastAPI 几十行，依赖库成熟
4. **扩展性好** — 未来可扩展为多 GPU worker

---

## 6. 报告大纲

报告 `./docs/distributed-asr-offload-research.md` 将包含：

1. **背景与问题陈述**
2. **技术方案对比**（方案 A/B/C 详细对比，含时效性、复杂度、可靠性矩阵）
3. **推荐方案详细设计**
   - 整体架构图
   - 协议设计（HTTP API 定义）
   - 服务发现机制（mDNS/Bonjour 在 Windows 上的实现选型）
   - 热插拔状态机（3 状态：OFFLINE → DISCOVERING → ONLINE → OFFLINE）
   - 与现有 `main.py` / `PipelineConfig` 的集成方式
4. **关键技术决策**
   - whisper.cpp CUDA backend vs faster-whisper CUDA vs whisper-rs
   - 音频传输 vs 本地提取（推荐本地 ffmpeg 提取，仅传 WAV）
   - 模型文件管理（预存在笔记本本地，不每次传输）
5. **Windows 特定考量**
   - mDNS 在 Windows 上的实现（python-zeroconf / dnssd）
   - 防火墙规则
   - 电源管理（笔记本合盖不休眠）
6. **性能预估**
   - 网络传输延迟 + GPU 推理时间 vs 本地 CPU 推理时间
   - 模型大小与 6GB 显存的适配
7. **风险与限制**
8. **实施路线图建议**（分阶段）

---

## 7. 验证

- 确认 `./docs/` 目录创建
- 确认报告文件内容完整、结构清晰
- 确认所有方案对比在报告中有据可查
