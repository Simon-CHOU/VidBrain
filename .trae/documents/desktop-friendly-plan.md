# VidBrain 桌面友好型长时运行方案

## 一、分析结论

**需要同时做参数优化（即时见效）和架构调优（长期可靠）。** 两者不冲突，分两个层级实施。

### 当前拓扑

```
[Watchdog线程] ─→ ThreadPoolExecutor(max_workers=1) ─→ process_pipeline() ─→ ASR(15线程, 3GB模型) → LLM(3次串行API)
                                                                                    ↑
[主线程定时循环] ─→ classify_all_pending() → process_batch() ─→ process_pipeline() ─┘
```

### 根因分析

| 瓶颈 | 原因 | 代码位置 |
|:---|:---|:---|
| **ASR CPU 几乎占满所有核心** | `cpu_threads` 默认值 `cpu_count()-1`，且 `large-v3` 模型 | `config.py:65`, `asr_engine.py:48,59` |
| **批内视频无冷却** | 5 个视频依次处理，无 `sleep` | `pipeline.py` process_batch 循环 |
| **未设置进程优先级** | Python 进程以 Normal 优先级运行，与桌面应用争抢 CPU | 全局缺失 |
| **Watchdog 无限速** | 大量新文件同时到达 → 无界任务队列积压 | `watcher.py:69-78` |
| **模型常驻 3GB** | `large-v3` 加载后永不释放 | `asr_engine.py:20` |

---

## 二、方案：参数层 + 架构层

### 2.1 参数层 — 修改默认值（即时生效，零风险）

| 参数 | 当前默认值 | 新默认值 | 理由 |
|:---|:---|:---|:---|
| `cpu_threads` | `cpu_count() - 1` | `max(1, min(2, cpu_count() // 4))` | 16核→2线程，4核→1线程，桌面应用总有CPU可用 |
| `model_size` | `large-v3` | `small` | small 模型约 500MB，large-v3 约 3GB；无人值守应牺牲少量精度换取稳定性 |

### 2.2 架构层 — 新增资源调控模块

新增 `vidbrain/throttle.py` 模块，提供三个核心能力：

```
throttle.py
├── set_low_priority()       # 将当前进程设为低优先级（Windows: BELOW_NORMAL）
├── apply_idle_priority()    # 将 ASR 线程设为 IDLE 优先级
└── cooldown(seconds)        # 批次间/视频间冷却
```

#### 2.2.1 进程优先级

使用标准库 `ctypes` 调用 Windows API（无需额外依赖）：

```python
import ctypes
# Windows: SetPriorityClass(GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)
# BELOW_NORMAL = 0x00004000, IDLE = 0x00000040
```

- 启动时自动将主进程设为 `BELOW_NORMAL_PRIORITY_CLASS`
- 可配置：`--priority normal|below_normal|idle`（默认 `below_normal`）

#### 2.2.2 视频间冷却

在 `process_batch()` 的视频循环中添加可配置的冷却间隔：

```python
for task in tasks:
    process_pipeline(...)
    if cfg.video_cooldown > 0:
        time.sleep(cfg.video_cooldown)  # 默认 30s
```

- 新 CLI 参数：`--video-cooldown 30`（秒，默认 0 保持向后兼容；长时运行推荐 30）

#### 2.2.3 Watchdog 速率限制

Watchdog 的 `on_closed` 增加去抖（debounce）+ 速率限制：

- 同一文件 5 秒内的重复事件忽略
- 新任务间隔 ≥ 2 秒
- 队列积压超过 20 个时暂停接收新任务

---

## 三、实施任务

### Task 1：修改默认值（config.py + main.py）
- [ ] 1.1 `config.py` 中 `model_size` 默认值 `large-v3` → `small`
- [ ] 1.2 `config.py` 中 `cpu_threads` 默认公式改为 `max(1, min(2, multiprocessing.cpu_count() // 4))`
- [ ] 1.3 `main.py` 中 `build_config` 同步更新（移除重复的 cpu_count 逻辑，统一用 config 的 factory）
- [ ] 1.4 更新 CLI help 文本体现新默认值

### Task 2：创建 throttle.py 模块
- [ ] 2.1 实现 `set_low_priority(level)` — 通过 ctypes 调用 Windows SetPriorityClass
- [ ] 2.2 实现 `set_asr_threads_low_priority()` — 将 ASR 线程池设为低优先级（若 faster-whisper 不支持，则仅设置进程级）
- [ ] 2.3 实现 `cooldown_sleep(seconds, reason)` — 带日志的 sleep，输出冷却原因
- [ ] 2.4 新增 CLI 参数 `--priority normal|below_normal|idle`（默认 `below_normal`）
- [ ] 2.5 新增 CLI 参数 `--video-cooldown`（秒，默认 0；长时运行推荐 30）

### Task 3：集成到管线
- [ ] 3.1 `main.py` 启动时调用 `set_low_priority(cfg.priority_level)`（在 `setup_logger()` 之后立即调用）
- [ ] 3.2 `process_batch()` 视频循环中添加 `cooldown_sleep(cfg.video_cooldown, ...)`（仅在 `cfg.video_cooldown > 0` 时）
- [ ] 3.3 `watcher.py` 的 `on_closed` 添加去抖逻辑：记录 `{file_path: last_event_time}`，5 秒内重复事件忽略
- [ ] 3.4 `watcher.py` 添加队列积压检查：`executor._work_queue.qsize() >= 20` 时跳过提交，记录 warning

### Task 4：Config + CLI 统一
- [ ] 4.1 `PipelineConfig` 新增 `priority_level: str = "below_normal"` 和 `video_cooldown: int = 0`
- [ ] 4.2 CLI 参数映射到 PipelineConfig
- [ ] 4.3 编写 `tests/test_throttle.py` — 至少验证 set_low_priority 不抛异常，cooldown_sleep 不阻塞

---

## 四、改动的文件

| 操作 | 文件 | 说明 |
|:---|:---|:---|
| **新增** | `vidbrain/throttle.py` | 资源调控模块 |
| **新增** | `vidbrain/tests/test_throttle.py` | 调控模块单元测试 |
| **修改** | `vidbrain/config.py` | 修改 model_size/cpu_threads 默认值 + 新增 priority_level/video_cooldown |
| **修改** | `vidbrain/main.py` | 新增 CLI 参数 + 启动时调用 set_low_priority + 管线集成 cooldown |
| **修改** | `vidbrain/watcher.py` | 去抖 + 队列积压保护 |

## 五、与现有参数的关系

| 新参数 | 与现有参数的关系 |
|:---|:---|
| `--priority` | 正交于所有参数，仅影响 OS 调度优先级 |
| `--video-cooldown` | 仅影响批内视频间间隔；与 `--interval`（批间间隔）互补 |
| `--model-size` 默认改为 `small` | 用户仍可通过 CLI 覆盖为 `large-v3` |
| `--cpu-threads` 默认收紧 | 用户仍可通过 CLI 覆盖 |

## 六、推荐的长时运行命令

```powershell
# 桌面友好型长时运行（新默认值生效）
uv run python -m vidbrain.main --vault-dir D:\vault --interval 30m

# 等价于显式指定
uv run python -m vidbrain.main --vault-dir D:\vault --interval 30m `
    --model-size small --cpu-threads 2 --priority below_normal --video-cooldown 30

# 如果需要更高精度（牺牲桌面体验）
uv run python -m vidbrain.main --vault-dir D:\vault --interval 2h `
    --model-size medium --cpu-threads 4 --video-cooldown 60
```

## 七、验证步骤

1. 语法检查：`uv run python -m py_compile` 所有修改文件
2. 单元测试：`uv run pytest vidbrain/tests/ -v` 全部通过
3. 优先级验证：运行时用任务管理器观察 Python 进程优先级是否为"低于正常"
4. 冷却验证：`--video-cooldown 5` 单视频运行，观察日志中间隔

## 八、决定性假设

1. **Windows 进程优先级 API 通过 ctypes 可用**：`SetPriorityClass` / `GetCurrentProcess` 是 kernel32.dll 导出函数，无需 psutil 依赖
2. **small 模型精度对长期无人值守可接受**：ASR 质量下降约 5-10%，但换来 6 倍内存节省和 3-4 倍 CPU 节省
3. **faster-whisper 的 cpu_threads 仅影响内部 CTranslate2 线程池**：进程优先级调整不会干预此行为
4. **Watchdog 去抖 5 秒足够**：Windows 文件系统事件通常在同一次写入中触发多次 on_closed，5 秒去抖能覆盖大部分场景
