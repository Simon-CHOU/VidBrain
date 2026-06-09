# VidBrain 远端 ASR 实施手册

## 1. 结论摘要

### 1.1 目标

在当前双机场景下，实现如下行为：

- `Desktop` 继续运行当前 VidBrain 主流程。
- `Laptop` 作为局域网内的远端 GPU ASR 节点，地址固定为 `192.168.5.123`。
- `Laptop` 在线时，ASR 优先使用远端 `RTX 2060 6GB`。
- `Laptop` 离线、休眠、断网或服务异常时，Desktop 自动回退到本地 `faster-whisper tiny` CPU 路径。
- `Laptop` 恢复接入局域网且服务恢复后，后续任务自动重新切回远端 GPU。

### 1.2 最终推荐

推荐采用：

- **数据面**：`Desktop -> HTTP -> Laptop whisper-server`
- **控制面**：固定 endpoint + 主动健康检查 + 熔断 + 冷却 + 后台恢复探测
- **服务实现**：优先 `whisper.cpp` 官方 `whisper-server`
- **回退路径**：Desktop 本地 `faster-whisper tiny`

不推荐采用：

- 纯 `SSH + 远程命令`
- 共享目录轮询
- 现在就上 `Consul / NATS / Triton / Riva`
- 现在就把 `mDNS` 作为唯一发现机制

### 1.3 为什么不是“只开 SSH 就够了”

`SSH` 只能解决：

- 远程登录
- 远程执行命令
- 远程拉日志
- 应急重启

但你真正需要的，是一个长期稳定的 ASR 服务面：

- 有固定接口
- 有明确超时
- 有可用性判定
- 有自动降级
- 有自动恢复
- 有长驻生命周期管理

因此结论是：

- **SSH 应保留，但只作为运维通道**
- **ASR 主链路必须是长驻服务 + 明确协议**

## 2. 架构建议

### 2.1 角色分工

#### Desktop

- 继续承担 VidBrain 主流程
- 继续用本地 `ffmpeg` 抽音频
- 把标准化后的 WAV 上传给 Laptop
- 远端失败时立即切回本地 CPU ASR

#### Laptop

- 只承担 ASR worker 角色
- 不承担数据库、watcher、agent graph、vault 写入等职责
- 本地常驻 GPU 版 Whisper 服务

### 2.2 推荐拓扑

```text
Desktop (VidBrain)
  ├─ 扫描视频
  ├─ ffmpeg -> 16kHz mono wav
  ├─ HTTP POST /inference -> 192.168.5.123:8080
  └─ 失败时 fallback -> local faster-whisper tiny (CPU)

Laptop (Win11 + RTX 2060 6GB)
  ├─ whisper-server.exe
  ├─ ggml-large-v3-turbo.bin / ggml-large-v3.bin
  ├─ 开机自启动
  └─ 监听 8080/tcp
```

### 2.3 热插拔语义

这里明确语义边界，避免后续实现时歧义：

- **自动降级**：远端请求失败时，当前任务级别回退到本地 CPU。
- **自动恢复**：远端恢复后，对后续任务重新启用远端 GPU。
- **不追求中途迁移**：正在执行中的一条转写任务，不做“半路从 Laptop 切回 Desktop”。

## 3. Laptop 端安装清单

### 3.1 必须安装

#### 1. Windows 11

- 保持当前 Win11 宿主机即可。
- 建议保持稳定更新，不使用过旧系统快照。

#### 2. NVIDIA 驱动

- 目标是让 `RTX 2060 6GB` 稳定可用。
- 优先选择稳定版驱动，不追求 beta。

#### 3. CUDA Toolkit

- `whisper.cpp` 官方 NVIDIA 路径要求先安装 `cuda`，再以 `-DGGML_CUDA=1` 构建。  
- 参考：<https://github.com/ggml-org/whisper.cpp/blob/master/README.md>

#### 4. whisper.cpp CUDA 可执行文件

至少需要：

- `whisper-server.exe`
- `whisper-cli.exe`

建议顺序：

1. 优先找可信的 Windows CUDA 预编译版本
2. 若没有，再在 Laptop 本机自行构建

#### 5. 模型文件

推荐准备：

- 首发：`ggml-large-v3-turbo.bin`
- 备选：`ggml-large-v3.bin`

模型必须：

- 常驻在 Laptop 本地
- 放在固定目录
- 不依赖 Desktop 每次下发

#### 6. 防火墙规则

至少放行：

- `8080/tcp`，用于 ASR HTTP 服务

#### 7. 自启动机制

必须保证：

- Win11 重启后服务自动恢复
- 无需用户登录也能工作

#### 8. 电源配置

必须避免：

- 合盖即休眠
- 网卡省电导致 LAN 不可达
- 长时间空闲后 GPU / 服务不可达却无恢复

### 3.2 建议安装

#### 1. OpenSSH Server

作用：

- 远程部署
- 看日志
- 手工重启
- 远程诊断

Windows 官方支持作为可选组件安装，并可通过：

- `Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0`
- `Start-Service sshd`
- `Set-Service -Name sshd -StartupType 'Automatic'`

同时需要 `OpenSSH-Server-In-TCP` 防火墙规则。  
参考：<https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse?tabs=powershell&pivots=windows-11>

#### 2. 固定地址能力

建议：

- 为 `192.168.5.123` 做 DHCP 保留
- 或保证主机名稳定解析

原因：

- 第一阶段不依赖 mDNS
- 固定地址比动态发现更稳

#### 3. 本地日志目录

建议保留：

- 服务启动日志
- 最近 N 次请求失败日志
- CUDA 初始化失败日志
- 模型加载失败日志

### 3.3 本阶段不需要安装

- `Consul`
- `NATS`
- `Docker`
- `Kubernetes`
- `Triton Inference Server`
- `NVIDIA Riva`
- `Bonjour`
- 独立 mDNS 服务

## 4. 推荐软件版本组合

## 4.1 组合 A：主栈

这是首选组合，目标是用最低复杂度得到稳定可运行的远端 GPU ASR。

| 项目 | 推荐 |
|---|---|
| OS | 现有 `Windows 11` |
| NVIDIA 驱动 | 稳定版驱动 |
| CUDA | `12.x`，优先 `12.4` |
| ASR 服务 | `whisper.cpp v1.8.6` |
| 模型 | `ggml-large-v3-turbo.bin` |
| 次选模型 | `ggml-large-v3.bin` |
| 启动管理 | Phase 1 用 `Task Scheduler` |
| 后续增强 | Phase 2 评估 `WinSW` |

选择理由：

- `whisper.cpp` 当前稳定版为 `v1.8.6`，官方 README 明确支持 Windows 与 NVIDIA GPU。  
- 官方也提供 `whisper-server` HTTP 服务示例。  
- 参考：<https://github.com/ggml-org/whisper.cpp/blob/master/README.md>  
- 参考：<https://github.com/ggml-org/whisper.cpp/blob/master/examples/server/README.md>

关于模型选择：

- `large-v3-turbo`：优先用于第一版上线，更适合速度/质量平衡
- `large-v3`：当你确认 GPU 负载可接受且更看重精度时再切

## 4.2 组合 B：备栈

只有当 `whisper.cpp` CUDA 路线在 Win11 上不稳时，才切换到备栈。

| 项目 | 推荐 |
|---|---|
| Python | `3.11` |
| ASR 引擎 | `faster-whisper` |
| CUDA | `12` |
| cuDNN | `9` |
| 模型 | `large-v3` 或 `distil-large-v3` |
| 服务层 | 轻量 FastAPI 壳 |

官方要求：

- Python `3.9+`
- GPU 路径需要 `cuBLAS for CUDA 12`
- GPU 路径需要 `cuDNN 9 for CUDA 12`

参考：<https://github.com/SYSTRAN/faster-whisper>

为什么不作为主栈：

- Windows 上 Python/CUDA/cuDNN/ctranslate2 兼容性更容易出问题
- 对当前双机场景来说，这条依赖链更脆

## 5. Win11 上的启动方式

### 5.1 第一推荐：Task Scheduler

这是第一阶段最务实的启动方式。

优点：

- Windows 自带
- 不引入额外第三方组件
- 支持 `ONSTART`
- 支持 `SYSTEM`
- 不依赖用户登录

微软官方 `schtasks create` 文档明确支持：

- 开机触发
- `SYSTEM` 身份
- Windows 11

参考：<https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/schtasks-create?source=recommendations>

推荐配置：

- Trigger：`At startup`
- Account：`NT AUTHORITY\SYSTEM`
- Run level：`Highest`
- 允许无登录运行
- Working directory 固定为服务目录

适用场景：

- 第一阶段 PoC
- 第一阶段正式试运行

局限：

- 更像“启动器”，不是完整服务管理器
- 进程中途崩掉后的自动治理能力有限

### 5.2 第二推荐：WinSW

当你希望 `whisper-server` 真正变成 Windows 服务时，再升级到 `WinSW`。

它的价值在于：

- 把任意可执行文件包装成 Windows service
- 支持：
  - `startmode`
  - `delayedAutoStart`
  - `depend`
  - 日志滚动
  - `onfailure restart`
  - `serviceaccount`

参考：

- <https://github.com/winsw/winsw>
- <https://github.com/winsw/winsw/blob/v3/docs/xml-config-file.md>

注意：

- 当前仓库信息显示 `2.x` 是稳定线，`3.x` 是预发布线
- 实施时优先选稳定发布版本，不建议追预发布

### 5.3 不推荐的启动方式

- 开机启动文件夹
- 登录后启动
- 纯 SSH 手工拉起

这些方式都不满足：

- 无人值守
- 开机自动恢复
- 稳定热插拔

## 6. Desktop / Laptop 最小协议设计

### 6.1 固定网络配置

第一阶段直接固定：

- Laptop 地址：`192.168.5.123`
- 端口：`8080`
- 协议：`HTTP/1.1`

不建议第一阶段就引入：

- mDNS
- zeroconf 自动发现
- 注册中心

### 6.2 数据面接口

直接复用 `whisper-server` 现成接口：

- `POST http://192.168.5.123:8080/inference`

官方 `examples/server` 已给出 `multipart/form-data` 用法。  
参考：<https://github.com/ggml-org/whisper.cpp/blob/master/examples/server/README.md>

#### 请求字段

最小请求建议如下：

| 字段 | 是否必须 | 值 |
|---|---|---|
| `file` | 必须 | Desktop 抽好的 WAV |
| `response_format` | 必须 | `json` |
| `language` | 建议 | `zh` |
| `temperature` | 可选 | `0.0` |
| `temperature_inc` | 可选 | `0.2` |
| `prompt` | 可选 | 上下文提示 |
| `carry_initial_prompt` | 可选 | `true` |

#### 音频规范

Desktop 应统一发送：

- `16kHz`
- `mono`
- `pcm_s16le`
- `wav`

这样做的原因：

- 与当前 VidBrain 本地 `ffmpeg` 边界一致
- 避免 Laptop 端再做解封装
- 降低服务端依赖

注意：

- `whisper-server` 可以使用 `--convert`
- 但启用 `--convert` 需要服务端安装 `ffmpeg`

所以第一阶段推荐：

- **Desktop 负责转 WAV**
- **Laptop 只做推理**

### 6.3 控制面接口

控制面分成两层。

#### 层 1：基础连通性

通过 TCP 探测：

- `192.168.5.123:8080`

用途：

- 快速判断服务端口是否打开

#### 层 2：应用就绪性

推荐最终具备：

- `GET /healthz`

建议返回：

```json
{
  "status": "ok",
  "model": "ggml-large-v3-turbo.bin",
  "backend": "cuda",
  "device": "RTX 2060 6GB",
  "uptime_sec": 12345,
  "queue_depth": 0,
  "version": "whisper.cpp-v1.8.6"
}
```

为什么需要：

- 端口通，不代表模型已加载完
- SSH 能连，不代表推理服务 ready
- 热插拔切换不能只依据“主机在线”

### 6.4 第一阶段的务实协议

如果第一天不想给 Laptop 再加额外 wrapper：

- **数据面**：直接用 `/inference`
- **控制面**：先用 TCP 探测 + 启动日志 + 失败即回退

也就是说：

- `/healthz` 是推荐项
- 不是第一天上线的硬前提

## 7. 状态机与切换规则

### 7.1 推荐状态机

Desktop 侧建议至少维护以下 4 个状态：

#### `REMOTE_ONLINE`

- 远端健康
- 新任务优先走 Laptop GPU

#### `REMOTE_DEGRADED`

- 最近出现超时、连接失败或服务异常
- 当前任务改走本地 CPU
- 进入冷却窗口

#### `REMOTE_OFFLINE`

- 连续探测失败
- 后续任务全部走本地 CPU

#### `REMOTE_RECOVERING`

- 后台探测恢复中
- 连续成功后再回到远端

### 7.2 推荐切换规则

第一版建议保守一些：

- 健康探测间隔：`10s`
- 连续 `2` 次失败：标记 `REMOTE_OFFLINE`
- 连续 `2` 次成功：恢复 `REMOTE_ONLINE`
- 熔断冷却时间：`60s`
- 单次连接超时：`1-2s`
- 推理请求超时：按音频时长动态配置，不使用固定短超时

### 7.3 回退语义

明确规定：

- 当前远端任务失败时，本条任务重新走本地 CPU
- 不要求半路迁移
- 远端恢复后，只影响后续任务

## 8. 返回结果兼容性

远端返回结果应尽量适配现有 `asr_engine.py` 的数据形状：

```json
[
  {
    "start": 0.0,
    "end": 1.8,
    "text": "第一句"
  },
  {
    "start": 1.8,
    "end": 3.6,
    "text": "第二句"
  }
]
```

这样做的收益：

- 下游 `pipeline.py` 几乎不用感知“本地转写”还是“远端转写”
- 只要把结果规约成统一结构即可

## 9. 一次性实施顺序

建议严格按顺序推进。

### 步骤 1：Laptop 本机环境

- 装 NVIDIA 驱动
- 装 CUDA
- 准备 `whisper.cpp` CUDA 版
- 准备 `ggml-large-v3-turbo.bin`

### 步骤 2：Laptop 本机验证

先只做单机验证：

- 跑 `whisper-cli`
- 确认模型能加载
- 确认 GPU 路径正常

然后再：

- 跑 `whisper-server`
- 本机 `curl /inference`

### 步骤 3：Laptop 自启动

第一阶段：

- 用 `Task Scheduler`

确保：

- 开机自动拉起
- 无需用户登录
- 有日志

### 步骤 4：Desktop 联通验证

在 Desktop 上验证：

- 能访问 `192.168.5.123:8080`
- 能上传一段 WAV
- 能收到 JSON 结果

### 步骤 5：切换策略接入

先实现：

- 远端优先
- 失败即本地 CPU

再实现：

- 后台探测恢复
- 熔断与冷却

### 步骤 6：最后再看 mDNS

只有在下面情况出现时，才需要补服务发现：

- Laptop IP 不再稳定
- DHCP 无法保留
- 局域网环境经常变化

## 10. 风险与注意事项

### 10.1 当前最大风险

不是算力，而是：

- Win11 上 CUDA 构建与运行稳定性
- 自启动后的长期服务稳定性
- 合盖/睡眠/网络恢复时的行为

### 10.2 第一阶段最应避免的事情

- 不要一开始就引入太多中间件
- 不要让 Laptop 兼做视频解封装
- 不要把 mDNS 当唯一真相源
- 不要用纯 SSH 当主协议

### 10.3 最务实的上线原则

先做到：

- 固定 IP
- 固定端口
- 固定模型
- 固定启动方式
- 失败即回退

然后再逐步增加：

- `/healthz`
- WinSW
- mDNS

## 11. 最终建议

如果现在就开始实施，建议直接按下面这套走：

1. Laptop 安装：
   - Win11
   - NVIDIA 稳定驱动
   - CUDA 12.4
   - `whisper.cpp v1.8.6`
   - `ggml-large-v3-turbo.bin`

2. Laptop 启动：
   - 第一阶段用 `Task Scheduler`
   - 第二阶段需要更强服务治理时再上 `WinSW`

3. Desktop/Laptop 协议：
   - 直接固定 `http://192.168.5.123:8080/inference`
   - Desktop 发标准 WAV
   - 远端失败即回退本地 `tiny`

4. 不做的事：
   - 不先上 mDNS
   - 不先上 SSH 主链路
   - 不先上重型 infra

这套方案最符合你现在的实际约束，也最容易先做出一个真正稳定、可热插拔的远端 GPU ASR 节点。
