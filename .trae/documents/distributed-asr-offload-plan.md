# 分布式 ASR 卸载方案研究计划

## Summary

本次不写代码，只产出研究报告，目标是为 VidBrain 设计一个“远端 GPU 优先、本地 CPU 兜底”的 ASR 架构：
- Desktop 继续承担视频扫描、ffmpeg 抽音频、管道调度。
- Laptop 作为可热插拔的 ASR 加速节点，使用 NVIDIA RTX 2060 6GB 跑 GPU 版 Whisper。
- Laptop 不在线时，agent ASR 自动回落到本机 CPU。
- Laptop 恢复接入局域网后，agent ASR 自动恢复使用远端 GPU。

计划输出报告到 `./docs/vidbrain-remote-asr-feasibility.md`。

## Current State Analysis

### 当前代码结构

本项目现有 ASR 切换点和容错边界已经比较清晰：

| 路径 | 现状 | 影响 |
|------|------|------|
| `vidbrain/main.py` | 通过 `cfg.asr_backend == "vulkan"` 在本地 CPU / 本地 Vulkan 二选一 | 还没有“远端优先、失败回退”的统一路由层 |
| `vidbrain/asr_engine.py` | `faster-whisper`，CPU `int8`，模型预加载和缓存逻辑完整 | 很适合继续作为稳定兜底路径 |
| `vidbrain/asr_engine_vulkan.py` | `whisper-cli` 子进程 + 本地文件音频提取 + 本地 Vulkan 检测 | 说明当前 GPU 方案是“单机内嵌式”，不是服务化方案 |
| `vidbrain/config.py` | `PipelineConfig.asr_backend` 目前是简单字符串 | 后续若扩展远端模式，需要引入更细粒度配置 |
| `vidbrain/pipeline.py` | ASR 在管道中是同步步骤，失败会影响整条视频任务 | 远端方案必须有超时、熔断、任务级重试策略 |

### 代码层关键事实

基于现有代码审查，后续设计必须尊重以下事实：
- `main.py` 里当前初始化流程假定 ASR 引擎在启动时即可确定，而不是运行时动态切换。
- `asr_engine_vulkan.py` 的 fallback 是“本地 Vulkan 失败后回到本地 CPU”，不是“远端节点失联后回本机 CPU”。
- `asr_engine_vulkan.py` 已经把 `ffmpeg` 抽音频放在本机执行，这为“Desktop 抽 WAV，再上传到 Laptop”提供了天然切分点。
- `asr_engine.py` 的 CPU 模型准备逻辑已经成熟，因此回退 CPU 的可行性很高。

### 原方案可行性判断

原始灵感“HTTP + mDNS 自动发现 + 远端 GPU”是可行的，理由如下：
- 你的场景是单 Desktop + 单 Laptop，拓扑很简单，不需要复杂分布式调度。
- 当前 ASR 已经是独立步骤，天然适合改造成“本地调用”或“远端 RPC 调用”。
- Laptop 的 RTX 2060 6GB 足以承担 `small` 到 `large-v3` 一类 Whisper 推理负载，至少在单并发/低并发下成立。
- 视频任务本来就是批处理，不要求几十毫秒级实时交互，因此 HTTP 文件上传完全够用。

但如果把“mDNS 发现成功”直接等同于“远端节点可用”，鲁棒性不足，主要问题是：
- Windows 上 mDNS/Bonjour 会受到防火墙、网络类型、睡眠恢复、多网卡影响，容易出现短暂误判。
- mDNS 只解决“发现”，不解决“模型是否已加载”“GPU 是否繁忙”“服务是否半死不活”。
- 如果 Laptop 合盖休眠或刚连回 Wi-Fi，服务注册和实际可处理请求之间常有空窗期。
- 如果 Desktop 在每个请求热解析服务，而无熔断/冷却，会产生抖动切换。

### RTX 2060 6GB 加速可行性与预估收益

先按你的上下文把“rtx2026”理解为 **RTX 2060 6GB**。

结合当前日志 `vidbrain.asr_engine - 使用本地缓存模型: tiny`，以及外部资料，本次研究先锁定一个很重要的结论：

#### 1. 能加速，但不是所有模型都值得

RTX 2060 6GB **可以运行 Whisper 的 GPU 路径**，容量上也能承载到 `large-v3` 级别的模型；外部资料显示该卡对 `large-v3` 属于“能跑”的范围，6GB VRAM 对单并发 Whisper 推理是够用的。

但“能跑”不等于“当前配置一定更快”，因为：
- 你当前日志显示实际运行的是 `tiny`。
- `tiny/base` 这类模型本来就很小，CPU 已经很快，GPU 初始化、CUDA 调度、网络传输会吃掉优势。
- 远端方案还要额外付出 Desktop->Laptop 上传音频的开销。

#### 2. 对 `tiny`，大概率没有值得期待的收益

一篇在 Windows 笔记本上用 `faster-whisper` 做的实测显示：
- `large-v3`：CPU 约 22s，GPU `int8` 约 11s，GPU `float16` 约 9.5s。
- `base`：GPU 约 4s，而 CPU 约 2s。
- `tiny`：与 `base` 接近，GPU 没明显优势。
- `medium`：CPU 与 GPU 都约 10s，收益接近于零。

这组数据虽然不是完全同机型同音频，但对你的决策非常有用：
- **`tiny/base` 上 GPU 可能反而更慢。**
- **真正拉开差距的是 `large-v3` 这类更大模型。**

#### 3. 对 `large-v3`，RTX 2060 值得认真考虑

官方 `faster-whisper` README 里的 GPU benchmark 说明了两件事：
- Whisper 的大模型在 GPU 上能获得明显吞吐优势。
- `int8` / `float16` 量化可以把显存压到单卡可承受范围。

虽然官方公开 benchmark 用的是更强的 RTX 3070 Ti 8GB，而不是 RTX 2060 6GB，但它至少证明了“Whisper 大模型上 GPU 明显值得做”。结合前面的 Windows 实测，可以把 RTX 2060 的实际预期粗略定为：

| 模型 | 对 RTX 2060 6GB 的判断 | 相对当前本机 CPU 的预期收益 |
|------|------------------------|------------------------------|
| `tiny` | 不推荐专门为它做远端 GPU | `0.7x ~ 1.1x`，常常不赚 |
| `base` | 基本不值得 | `0.8x ~ 1.2x` |
| `small` | 边界可用，取决于音频长度与 CPU | `1.0x ~ 1.5x` |
| `medium` | 有可能开始受益，但不稳定 | `1.0x ~ 2.0x` |
| `large-v3` | 明显最值得 | `约 2x`，乐观时可到 `2x+` |

这里的区间是研究期估算，不作为生产承诺；真正落地前仍需要用你自己的典型视频样本做 A/B。

#### 4. 对当前项目的最关键含义

如果 VidBrain 当前长期跑的是 `tiny`：
- **先不应该优先做远端 GPU 服务化**，因为收益可能非常小。
- **先应该确认为什么现在会落到 `tiny`**，以及这是不是为了“保命式跑通”而做的临时设置。

如果你愿意把目标模型提升到 `small`/`medium`/`large-v3`：
- Laptop 的 RTX 2060 6GB 才真正有价值。
- 远端 GPU 方案的 ROI 会从“可能没感觉”变成“很可能有感”。

## Proposed Changes

### 研究报告结论方向

报告将不再把“纯 HTTP + mDNS”作为最终推荐，而是升级为更稳的混合方案：

**推荐主方案：静态目标 + 主动健康检查 + 可选 mDNS 的混合控制面**

架构拆分为两层：

1. 数据面：
   - Desktop 本地完成 `ffmpeg` 抽音频。
   - Desktop 将 WAV 发送给 Laptop 上的 ASR 服务。
   - Laptop 返回与现有 `transcribe()` 兼容的 JSON 片段结果。

2. 控制面：
   - 以“主动健康检查”决定是否使用远端 GPU。
   - 以“静态配置主机名/IP”为主发现方式。
   - 以“mDNS/zeroconf”为辅助手段，只负责发现，不直接驱动切换。
   - 以“熔断 + 冷却 + 后台恢复探测”避免来回抖动。

### 为什么这个方案比原方案更稳

这个混合方案比“HTTP + mDNS 即切换”更 robust，原因是：
- 发现与可用性解耦：节点被发现，不代表模型已就绪；健康检查能补上这层语义。
- 切换与请求解耦：请求失败时立即回退本地 CPU，但远端恢复由后台探测决定，不阻塞主任务。
- 临时故障可吸收：Wi-Fi 漫游、笔记本唤醒、驱动重置都可以落到“degraded + cooldown”状态，而不是不断抖动。
- Windows 兼容性更好：即使 mDNS 偶发失灵，只要固定主机名或固定 DHCP 租约还在，系统仍可工作。

### 候选方案对比

#### 方案 A：HTTP + mDNS

优点：
- 实现最轻。
- 适合两台机器的小型 homelab。
- 与现有同步批处理 ASR 最贴合。

缺点：
- 只靠 mDNS 不够稳。
- 没有标准的服务健康与注册中心语义。
- 需要自己补齐超时、熔断、恢复逻辑。

结论：
- 可行，但只能作为“轻量实现骨架”，不能直接当最终稳态设计。

#### 方案 B：SSH + SCP 远程执行

优点：
- 安全边界清晰。
- 不需要额外长期运行的 HTTP 服务。

缺点：
- 交互链路长，上传、执行、下载耦合严重。
- 热插拔体验差，断开时错误恢复不优雅。
- 远端服务状态不可观测，健康检查难做细。

结论：
- 不推荐作为主方案，适合一次性脚本，不适合长期 daemon。

#### 方案 C：共享目录 + 轮询

优点：
- 实现概念简单。
- 不依赖 HTTP/gRPC。

缺点：
- 轮询带来高延迟和状态不一致。
- 文件锁、残留临时文件、网络共享权限会很脆弱。
- 热插拔最难做优雅。

结论：
- 最不稳，不推荐。

#### 方案 D：HTTP 服务 + 健康检查 + 熔断 + 可选服务发现

优点：
- 对当前项目最匹配。
- 既支持自动升级，也支持自动降级。
- 运行时状态可建模，可观测性最好。

缺点：
- 需要额外定义状态机和探测逻辑。
- 仍然需要自己做轻量运维约束。

结论：
- 这是本次研究的最终推荐方案。

## 现成 Infra / Middleware 调研结论

### 最值得优先复用的现成件

#### 1. `whisper.cpp` 官方 HTTP server

调研发现 `whisper.cpp` 已经自带 HTTP server，并支持 OpenAI 兼容接口；其服务模型偏轻量，适合作为 Laptop 侧的最小可用推理服务骨架。已知特点包括：
- 官方项目自带服务端能力。
- 适合批量文件转写的 HTTP 调用模式。
- 服务内部偏单机串行推理模型，对你这类单 GPU 节点是可接受的。

研究报告将把它列为 **Laptop 侧首选服务内核**。

#### 2. `faster-whisper` OpenAI 兼容服务实现

社区里已有多种基于 FastAPI 的 `faster-whisper` 服务封装，能直接暴露 OpenAI 风格的转写接口，并自动在 CPU/CUDA 间选择设备。

研究报告将把它列为 **备选服务内核**，优势是：
- 与当前 `vidbrain/asr_engine.py` 技术栈更接近。
- 如果未来放弃 `whisper.cpp`，迁移成本更低。

但它通常是社区封装，成熟度和“官方背书”弱于 `whisper.cpp` 自带 server。

### 不适合当前双机场景的现成 infra

#### 3. Consul

Consul 的服务发现、健康检查、DNS/HTTP 接口都非常强，理论上能优雅解决“服务注册 + 健康状态 + 自动摘流”问题。

但对当前场景而言：
- 只有 2 台机器，部署和维护成本偏高。
- 你不需要真正的服务网格、KV、ACL、跨数据中心能力。
- 对 Windows 家庭局域网环境来说，收益显著小于复杂度。

研究结论：
- **可作为将来扩容到多节点时的升级路径**，本阶段不推荐落地。

#### 4. NATS

NATS 在 request-reply、自动重连、轻量消息总线方面非常强，适合异步任务分发。

但对当前需求而言：
- 你需要的是“同步拿回 ASR 结果”的推理服务，而不是消息总线本身。
- NATS 不能直接替代 ASR server 和健康注册中心。
- 如果只为两台机器引入它，系统会比 HTTP RPC 更复杂。

研究结论：
- **适合未来做异步队列化、多 worker、多任务负载均衡时再考虑**，当前不推荐。

#### 5. Triton Inference Server

Triton 是成熟的生产级推理服务框架，并支持批处理和高性能后端。

但对当前场景明显过重：
- Whisper/TensorRT 的构建链很复杂。
- 更适合 Linux + Docker + 专用 GPU 服务机。
- 单 RTX 2060 6GB、单 worker、家庭局域网，不需要这种级别的 infra。

研究结论：
- **过度工程**，不推荐。

#### 6. NVIDIA Riva

Riva 是完整的企业级语音平台，但：
- 主要面向 Linux/NVIDIA 官方支持路径。
- 官方建议资源明显高于当前 6GB 单卡的舒适区。
- 引入成本和运维复杂度远超你的需求。

研究结论：
- **不适合本项目**。

#### 7. WhisperLive / WhisperLiveKit

它们提供较完整的 WebSocket/REST/流式能力，适合实时语音和多客户端场景。

但当前项目是视频文件离线 ASR 批处理：
- 流式能力不是刚需。
- 依赖和运行形态更复杂。
- 对本项目的收益不如轻量 HTTP server 明显。

研究结论：
- **可参考接口设计，但不作为首选落地件**。

## Assumptions & Decisions

### 已定决策

为让执行阶段不再需要二次决策，本次计划先锁定以下结论：

1. **远端节点角色**
   - Laptop 只做 ASR worker，不承担数据库、watcher、agent graph 等其他角色。

2. **音频边界**
   - Desktop 本地抽 WAV。
   - 网络上传输的是已标准化的单声道 16kHz WAV，而不是整个视频文件。

3. **回退语义**
   - 远端失败时回退发生在“任务级”，不是“同一个转写会话中段无缝迁移”。
   - 如果远端处理到一半断开，本条任务重新走本机 CPU。

4. **切换语义**
   - “自动升级到 Laptop GPU”发生在下一条任务开始前，不追求对正在运行中的任务做中途切换。

5. **发现语义**
   - 主发现方式是固定主机名 / 固定 DHCP 租约 / 配置化 endpoint。
   - mDNS 只做附加便利，不作为唯一真相源。

6. **服务端首选实现**
   - Laptop 侧优先研究基于 `whisper.cpp` 官方 server 的方案。
   - `faster-whisper` OpenAI 兼容服务作为第二候选。

7. **客户端鲁棒性策略**
   - 必须包含：请求超时、失败即回退、熔断、冷却窗口、后台恢复探测。

### 执行阶段将涉及的具体文件

本次只是研究，不写代码；但后续若要实现，变更面预期如下：

| 文件 | 变更类型 | 原因 |
|------|----------|------|
| `vidbrain/config.py` | 扩展 | 增加远端 ASR endpoint、探测间隔、超时、熔断等配置 |
| `vidbrain/main.py` | 改造 | 从本地二选一初始化改成“本地 CPU + 远端 GPU 路由器” |
| `vidbrain/asr_engine.py` | 复用 | 继续作为本机 CPU fallback |
| `vidbrain/asr_engine_vulkan.py` | 参考或收敛 | 可保留本地 Vulkan 路径，但不再承担远端方案主角色 |
| `vidbrain/pipeline.py` | 轻改 | 处理远端失败时的任务级重试和错误归因 |
| `vidbrain/tests/` | 补充 | 增加远端不可达、恢复上线、熔断冷却等测试 |
| `docs/vidbrain-remote-asr-feasibility.md` | 新增 | 最终研究报告 |

## Laptop 端安装与运维要求

### 结论先行

在你当前约束下，**不能只依赖让 Desktop 端 agent SSH 到 Laptop Win11 宿主机**。

原因不是“SSH 不能跑命令”，而是它只解决了以下问题中的一小部分：
- 远程登录
- 远程启动进程
- 文件上传下载

但你的目标真正需要的是：
- 稳定的转写接口
- 明确的超时语义
- 健康检查
- 自动降级
- 自动恢复
- 长驻服务生命周期管理

因此，**SSH 适合做运维通道，不适合做主数据面协议**。

### 推荐形态：Laptop 端最小安装集

如果采用推荐方案 `whisper.cpp server + 主动健康检查 + 本地 CPU fallback`，Laptop 端需要的最小安装集如下：

#### 必需项

1. **NVIDIA 显卡驱动**
   - 用于确保 RTX 2060 正常工作。

2. **`whisper.cpp` 的 Windows CUDA 可执行文件**
   - 最理想是直接使用带 `GGML_CUDA` 的预编译 `whisper-server.exe` / `whisper-cli.exe`。
   - 如果没有合适预编译包，则需要在 Win11 上自行构建。

3. **CUDA 运行环境**
   - `whisper.cpp` 的 CUDA 构建需要本机具备对应 CUDA 运行时/Toolkit。
   - 若走 `faster-whisper` 路线，则需要匹配的 CUDA 12 + cuDNN 9 运行库。

4. **Whisper 模型文件**
   - 如果用 `whisper.cpp`，需要 `ggml-large-v3.bin` 或 `ggml-large-v3-turbo.bin` 之类的本地模型文件。
   - 模型应常驻在 Laptop 本地，不通过每次任务动态下发。

5. **常驻服务启动方式**
   - 至少需要一个后台自启动机制，让 ASR 服务在 Win11 重启后自动拉起。
   - 可接受形式包括：计划任务、Windows Service 包装器、开机启动脚本。

6. **Windows 防火墙入站规则**
   - 放行 ASR 服务监听端口，例如 `8080`。

7. **电源设置**
   - 若你希望 Laptop 在“回家接入局域网后立刻恢复可用”，就必须避免合盖即休眠、避免网卡睡眠导致服务掉线。

#### 强烈建议项

1. **固定地址能力**
   - 最好为 `192.168.5.123` 做 DHCP 保留，或固定主机名解析。
   - 这样 Desktop 侧可优先直连固定 endpoint，而不是完全依赖 mDNS。

2. **OpenSSH Server**
   - 用于远程部署、查看日志、重启服务、应急诊断。
   - 这是运维便利项，不是 ASR 主协议本身。

3. **轻量健康接口**
   - 例如 `GET /healthz` 返回模型名、设备、是否 ready、队列长度。
   - 用来支撑 Desktop 侧自动切换。

4. **日志落盘**
   - 至少保留服务启动日志和最近一次推理错误日志，便于排查 CUDA/模型/端口问题。

### 两条落地路径的安装差异

#### 路径 A：`whisper.cpp` server

这是当前最推荐的 Laptop 方案。

Laptop 侧需要：
- NVIDIA 驱动
- CUDA Toolkit 或与预编译包匹配的 CUDA 运行环境
- `whisper-server.exe`
- `ggml-large-v3*.bin` 模型文件
- 端口放行
- 自启动机制

可选需要：
- `OpenSSH Server`
- `ffmpeg`

其中 `ffmpeg` 在该方案里 **不是刚需**，因为推荐由 Desktop 先抽成标准 `wav` 再上传，Laptop 不负责视频解封装。

#### 路径 B：`faster-whisper` API server

这是备选方案。

Laptop 侧需要：
- Python 3.10+/venv
- `faster-whisper`
- CUDA 12 + cuDNN 9 运行库
- 模型缓存目录
- 提供 HTTP API 的服务壳层
- 自启动机制

它的优点是更接近你现有 `vidbrain/asr_engine.py`；缺点是 Windows 上 CUDA/cuDNN 依赖链比 `whisper.cpp` 裸 server 更脆一些。

### 为什么“只有 SSH”不够

如果只做：
- Desktop 用 SSH 登录 Laptop
- 远程执行一次 `whisper-cli`
- 再把结果拉回来

理论上可以跑，但它不满足你要的“无感热插拔”：

1. **不可观测**
   - SSH 连得上，不代表模型已加载、GPU 可用、服务 ready。

2. **失败语义很差**
   - 网络抖动、权限问题、远端进程半死不活时，错误边界不清楚。

3. **切换成本高**
   - 每个任务都要建连接、发命令、等进程退出，抖动比常驻 HTTP 服务大很多。

4. **恢复策略差**
   - 你没法优雅做“熔断 60 秒后后台再探活”，只能不断尝试 SSH。

5. **并发与排队难管理**
   - 多任务时，远端命令执行、临时文件清理、stdout/stderr 抓取都会变得脆。

所以正确的判断是：
- **SSH 可以有，但应只承担运维角色。**
- **ASR 主链路仍然要有一个明确协议和长驻服务。**

### 对你当前网络环境的直接建议

既然你已经知道 Laptop 当前地址是 `192.168.5.123`，那第一阶段完全没必要先上服务发现系统。

更稳的阶段化建议是：

1. **阶段 1：固定 endpoint**
   - Desktop 直接把远端 ASR 目标写成 `http://192.168.5.123:<port>`。

2. **阶段 2：健康检查驱动切换**
   - Desktop 后台定期探测 `/healthz`。
   - 探测成功才切到 Laptop。
   - 探测失败即回落本地 CPU，并进入冷却窗口。

3. **阶段 3：再补 mDNS**
   - 只把 mDNS 当作“地址变化时的辅助发现”。
   - 不是首阶段必需条件。

这会比“先折腾 SSH 或先折腾 mDNS”更稳。

## 实施手册草案

### 1. 总体推荐

先给出不再需要二次决策的推荐结论：

#### 主方案

- **Laptop 端**
  - Win11 上常驻 `whisper.cpp` 的 `whisper-server`
  - 使用 NVIDIA CUDA 路径
  - 本地常驻 `ggml-large-v3-turbo.bin` 或 `ggml-large-v3.bin`
- **Desktop 端**
  - 继续运行当前 VidBrain
  - 本地 `ffmpeg` 先抽成 16kHz / mono / PCM s16le WAV
  - 把 WAV 通过 HTTP 发到 `http://192.168.5.123:8080/inference`
  - 请求失败则回退本地 `faster-whisper tiny`
- **控制面**
  - 第一阶段不用 mDNS
  - 直接固定 endpoint：`192.168.5.123`
  - 通过健康检查 + 熔断 + 冷却实现自动降级与自动恢复

#### 备方案

- 如果 `whisper.cpp` CUDA 在 Win11 上构建或运行不稳定，再切到：
  - `Python 3.11 + faster-whisper + CUDA 12 + cuDNN 9 + FastAPI 服务壳`

这个备方案不是首选，原因是 Windows 上 Python + CUDA + cuDNN 的依赖链更脆，而 `whisper.cpp` 的 server 本身已经具备直接可用的 HTTP 服务能力。[whisper-server README](https://github.com/ggml-org/whisper.cpp/blob/master/examples/server/README.md)

### 2. Laptop 端安装清单

以下清单按“必须 / 建议 / 不需要”分层。

#### 2.1 必须安装

1. **Windows 11**
   - 维持当前 Win11 宿主机即可。
   - 建议至少保持在稳定更新状态，避免老旧驱动和可选组件问题。

2. **NVIDIA 驱动**
   - 目标是让 `RTX 2060 6GB` 在本机稳定可见、无异常降频、无频繁驱动重置。
   - 优先选稳定分支驱动，不追求最新 beta。

3. **CUDA Toolkit**
   - `whisper.cpp` 官方 NVIDIA 路径要求先安装 `cuda`，然后以 `-DGGML_CUDA=1` 构建。[whisper.cpp](https://github.com/ggml-org/whisper.cpp/blob/master/README.md)

4. **`whisper.cpp` 可执行文件**
   - 需要至少具备：
     - `whisper-server.exe`
     - `whisper-cli.exe`
   - 如果拿得到可信的预编译 CUDA 版本，优先直接用预编译。
   - 如果拿不到，再自行编译。

5. **Whisper 模型文件**
   - 首选：
     - `ggml-large-v3-turbo.bin`
   - 次选：
     - `ggml-large-v3.bin`
   - 模型应放在 Laptop 本地固定目录，不应由 Desktop 每次上传。

6. **防火墙放行**
   - 放行服务监听端口，例如 `8080/tcp`。

7. **自启动机制**
   - 确保 Windows 重启后服务自动起来。

8. **电源配置**
   - 禁止“合盖即休眠”。
   - 禁止网卡被省电策略过早挂起。
   - 目标是“回家接上局域网后即可恢复服务可用”。

#### 2.2 建议安装

1. **OpenSSH Server**
   - 用于远程部署、日志排查、人工重启服务。
   - Windows 官方支持把 OpenSSH Server 作为可选功能安装，并可用 `Start-Service sshd` 与 `Set-Service -Name sshd -StartupType 'Automatic'` 管理，同时需要 `OpenSSH-Server-In-TCP` 防火墙规则。[Microsoft Learn](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse?tabs=powershell&pivots=windows-11)

2. **固定地址能力**
   - 最好把 `192.168.5.123` 做 DHCP 保留。
   - 或至少确保 Laptop 主机名可稳定解析。

3. **本地日志目录**
   - 保存：
     - 服务启动日志
     - 最近 N 次请求错误日志
     - CUDA/模型加载失败日志

#### 2.3 本阶段不需要安装

- `Consul`
- `NATS`
- `Docker/Kubernetes`
- `Triton Inference Server`
- `NVIDIA Riva`
- `Bonjour`
- 独立 mDNS 守护进程

### 3. 推荐软件版本组合

这里给出两套“够稳、可实施”的版本组合。

#### 3.1 组合 A：推荐主栈

**目标**
- 以最低复杂度把 Laptop 变成稳定 GPU ASR 节点。

**版本建议**
- **OS**：现有 `Windows 11`
- **GPU 驱动**：NVIDIA 稳定版驱动
- **CUDA**：`CUDA 12.x`，优先 `12.4`
- **ASR 服务**：`whisper.cpp v1.8.6`
- **模型**：
  - 首发：`ggml-large-v3-turbo.bin`
  - 精度优先时切换：`ggml-large-v3.bin`
- **启动管理**：
  - Phase 1：Windows `Task Scheduler`
  - Phase 2：如需更强服务语义，再引入 `WinSW`

**推荐理由**
- `whisper.cpp` 当前稳定版为 `v1.8.6`，官方 README 明确支持 Windows 与 NVIDIA GPU，并可通过 `whisper-server` 直接提供 HTTP 服务。[whisper.cpp](https://github.com/ggml-org/whisper.cpp/blob/master/README.md)
- `examples/server` 文档已经给出了服务启动参数、`/inference` 与 `/load` 等接口示例。[whisper-server README](https://github.com/ggml-org/whisper.cpp/blob/master/examples/server/README.md)
- `large-v3-turbo` 更适合你这种“远端 GPU 节点 + 批处理”场景，通常是更好的速度/质量平衡点。

#### 3.2 组合 B：备用栈

**目标**
- 当 `whisper.cpp` CUDA 路线在 Laptop 上不稳定时，保留一套 Python 备份方案。

**版本建议**
- **Python**：`3.11`
- **ASR 引擎**：`faster-whisper`
- **CUDA**：`CUDA 12`
- **cuDNN**：`cuDNN 9`
- **模型**：
  - `large-v3`
  - 或 `distil-large-v3`

**推荐理由**
- `faster-whisper` 官方要求 Python `3.9+`，GPU 路径要求 `cuBLAS for CUDA 12` 与 `cuDNN 9 for CUDA 12`。[faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- README 中也明确给出了 `large-v3` 在 `device="cuda"`、`compute_type="float16"` 或 `int8_float16` 下的运行方式。[faster-whisper](https://github.com/SYSTRAN/faster-whisper)

**不作为主栈的原因**
- Windows 上 `faster-whisper` 的 GPU 依赖链更容易受到 `cudnn*.dll`、`ctranslate2` 版本兼容的影响。
- 你当前目标是做一个长期可热插拔的 LAN worker，优先减少 Python/CUDA 生态的额外脆弱点。

### 4. Win11 上的启动方式

启动方式分为两档。

#### 4.1 第一推荐：Task Scheduler

这是首阶段最务实的方式，因为：
- 是 Windows 自带组件。
- 不引入额外 wrapper。
- 支持 `ONSTART` 触发。
- 支持 `SYSTEM` 身份与高权限运行。

微软官方 `schtasks` 文档明确支持：
- `ONSTART` 开机触发
- 使用 `SYSTEM` 身份
- 不依赖用户登录会话。[schtasks create](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/schtasks-create?source=recommendations)

**建议配置**
- Trigger：`At startup`
- Account：`NT AUTHORITY\SYSTEM`
- Run level：`Highest`
- Run whether user is logged on or not：启用
- Working directory：固定为服务目录
- 失败后：允许任务历史和重试

**适用阶段**
- 第一阶段 PoC
- 第一阶段生产试运行

**局限**
- 它更像“启动器”，不是完整的服务管理器。
- 对“进程运行中途异常退出后自动拉起”的服务治理能力不如真正的 Windows Service wrapper。

#### 4.2 第二推荐：WinSW

如果你想让 `whisper-server` 更像真正的 Windows 服务，推荐后续升级到 `WinSW`。

原因：
- 它专门用于把任意可执行文件包装成 Windows service。
- 官方文档支持：
  - `startmode`
  - `delayedAutoStart`
  - `depend`
  - 日志滚动
  - `onfailure restart`
  - `serviceaccount`
[WinSW](https://github.com/winsw/winsw) [WinSW XML config](https://github.com/winsw/winsw/blob/v3/docs/xml-config-file.md)

**推荐用途**
- 第二阶段稳定化
- 需要明确的服务依赖与自动重启时
- 需要更清晰的服务日志管理时

**注意**
- 当前仓库信息显示：GitHub Releases 里 `2.x` 为稳定线，`3.x` 为预发布线。
- 因此实施时优先选稳定发布版本，不建议盲追预发布。

#### 4.3 不推荐的方式

- 开机启动文件夹
- 用户登录后再启动
- 纯 SSH 远程手工拉起

这些都不满足你要的“无感热插拔 + 无人值守”。

### 5. Desktop / Laptop 之间的最小协议设计

这里给出一个尽量贴近现成 `whisper-server` 的最小协议，不额外引入重型组件。

#### 5.1 网络拓扑

- Desktop：当前 VidBrain 主机
- Laptop：`192.168.5.123`
- 端口：`8080/tcp`
- 协议：`HTTP/1.1`

#### 5.2 数据面协议

**推荐 endpoint**
- `POST http://192.168.5.123:8080/inference`

这是 `whisper.cpp/examples/server` 已有的标准接口，支持 `multipart/form-data` 上传音频文件。[whisper-server README](https://github.com/ggml-org/whisper.cpp/blob/master/examples/server/README.md)

**请求内容**
- `file`
  - Desktop 预先抽好的 `wav`
- `response_format=json`
- `language=zh`
  - 若需要自动检测则再改为 `auto`
- 可选参数：
  - `prompt`
  - `carry_initial_prompt=true`
  - `temperature=0.0`
  - `temperature_inc=0.2`

**音频规范**
- Desktop 侧统一转换成：
  - `16kHz`
  - `mono`
  - `pcm_s16le`
  - `wav`

理由：
- 这正好与现有本地 `ffmpeg` 边界一致。
- 减少 Laptop 端再解封装和再转换的不确定性。
- `whisper.cpp` 文档也以 WAV 输入为主；如果要让服务端自己转换，则需要服务端启用 `--convert`，而这又要求服务端安装 `ffmpeg`。[whisper-server README](https://github.com/ggml-org/whisper.cpp/blob/master/examples/server/README.md)

**结论**
- 第一阶段不建议让 Laptop 负责 `ffmpeg` 转换。
- 让 Desktop 发标准 WAV，是最稳的最小协议。

#### 5.3 控制面协议

最小控制面建议包含两个探测层级。

**层级 1：基础连通性**
- TCP connect 到 `192.168.5.123:8080`

**层级 2：应用就绪性**
- 推荐实现一个轻量 `GET /healthz`
- 返回字段建议：
  - `status`: `ok|degraded|loading`
  - `model`: `ggml-large-v3-turbo.bin`
  - `backend`: `cuda`
  - `device`: `RTX 2060 6GB`
  - `uptime_sec`
  - `queue_depth`
  - `version`

**为什么需要 `/healthz`**
- 只看端口通不通，不足以判断模型是否加载完成。
- 只看 SSH 能不能连，更不足以判断推理服务是不是 ready。

#### 5.4 第一阶段的务实做法

如果第一阶段你不想在 Laptop 端增加额外 wrapper：
- **数据面**：直接调用 `whisper-server` 的 `/inference`
- **控制面**：先用
  - TCP 端口探测
  - Windows 启动管理日志
  - 失败即回退

也就是说：
- `/healthz` 是推荐项
- 但不是第一天上线的硬阻塞项

#### 5.5 状态机约定

Desktop 端建议维护以下状态：

1. `REMOTE_ONLINE`
   - 远端可用，优先走 Laptop GPU

2. `REMOTE_DEGRADED`
   - 最近出现超时/连接失败
   - 当前任务直接切回本地 CPU
   - 进入冷却窗口

3. `REMOTE_OFFLINE`
   - 连续多次探测失败
   - 后续任务全部使用本地 CPU

4. `REMOTE_RECOVERING`
   - 后台探测恢复
   - 连续多次成功后再切回远端 GPU

#### 5.6 推荐的切换规则

第一版建议直接采用保守规则：
- 健康探测间隔：`10s`
- 连续 `2` 次失败：进入 `REMOTE_OFFLINE`
- 连续 `2` 次成功：进入 `REMOTE_ONLINE`
- 熔断冷却时间：`60s`
- 单次连接超时：`1-2s`
- 推理请求超时：
  - 按音频时长动态配置
  - 不要使用固定的过短超时

#### 5.7 返回结果格式

Desktop 最终消费的数据结构应尽量与现有 `asr_engine.py` 返回值兼容：

```json
[
  {
    "start": 0.0,
    "end": 3.2,
    "text": "..."
  }
]
```

这样后续真正接入时，只需要把“本地转写结果”和“远端 HTTP 返回结果”对齐到同一抽象层，而不需要重改 `pipeline.py` 的下游逻辑。

### 6. 一次性实施顺序

为了让后续执行阶段更顺，推荐严格按下面顺序落地：

1. **Laptop 基础环境**
   - 装 NVIDIA 驱动
   - 装 CUDA
   - 准备 `whisper.cpp` CUDA 版
   - 准备 `ggml-large-v3-turbo.bin`

2. **Laptop 本机单机验证**
   - 先本机直接跑 `whisper-cli`
   - 再本机跑 `whisper-server`
   - 再本机 `curl /inference`

3. **Laptop 自启动**
   - Phase 1 用 `Task Scheduler`
   - 记录启动日志与端口监听状态

4. **Desktop 到 Laptop 网络验证**
   - Desktop 访问 `192.168.5.123:8080`
   - 验证一次完整 WAV 上传与 JSON 返回

5. **Desktop 端切换策略**
   - 先做“远端优先，失败即本地 CPU”
   - 再加后台探测恢复

6. **最后再决定是否补 mDNS**
   - 只有当 Laptop 地址不再稳定时，才补服务发现

### 7. 最终报告目标

最终写入 `./docs/vidbrain-remote-asr-feasibility.md` 的实施手册部分，应至少覆盖：
- Laptop 端安装清单
- 推荐版本组合
- Win11 启动方式主次排序
- 最小协议设计
- 热插拔状态机
- 一次性实施顺序
- 失败时的回退语义
- 为什么 SSH 只能做运维而不能做主链路

## Verification

执行阶段完成后，按以下标准验收：
- `./docs/vidbrain-remote-asr-feasibility.md` 已创建，且内容覆盖可行性、备选方案、现成 infra、推荐结论。
- 报告明确回答“原方案是否可行”“为什么不够 robust”“更稳方案是什么”“为什么不选 Consul/NATS/Triton/Riva”。
- 报告包含明确的推荐栈排序：
  1. `whisper.cpp` server + 主动健康检查 + 熔断 + 可选 mDNS
  2. `faster-whisper` API server + 同样的控制面
  3. 其余中间件仅作为未来扩容路径
- 报告明确热插拔语义：远端离线后任务级回退到 CPU，远端恢复后对后续任务自动重新启用。
