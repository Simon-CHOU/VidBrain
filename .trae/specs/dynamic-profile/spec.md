# Dynamic Performance Profile Spec

## Why
当前程序通过 `--cpu-threads` 和 `--priority` 参数静态设置 CPU 消耗，需要人工干预。实际使用场景中，desktop 的使用状态在"有人操作"和"长时间无人值守"之间动态切换。需要一个智能的、自动化的性能调度策略：desktop 活跃时自动降速避免拖累用户体验，desktop 闲置时自动满负荷利用硬件资源加速处理。

## What Changes

### 核心变更
- **vidbrain/throttle.py**: 新增 `PerformanceProfile` 状态机类和 Windows 桌面空闲检测（`GetLastInputInfo` API）
- **vidbrain/config.py**: `PipelineConfig` 新增 `--profile` CLI 参数和 profile 配置字段
- **vidbrain/main.py**: 主循环中集成 profile 状态检测与动态切换，根据当前 profile 调整 `parallel_workers` 和 `cpu_threads`

### 行为变更
- 移除对 `--priority` 的独立依赖，priority 由 profile 自动管理
- 启动时默认进入 `idle` 状态（满负荷），随后根据桌面活动自动切换

## Impact
- Affected specs: checkpoint-resume（退出时状态不影响 profile）
- Affected code: `vidbrain/throttle.py`, `vidbrain/config.py`, `vidbrain/main.py`

---

## ADDED Requirements

### Requirement R1: Profile 定义

系统 SHALL 支持两个预定义的性能 profile，每个 profile 对应一组确定的运行参数。

#### Profile "idle"（无人值守模式）
| 参数 | 值 | 说明 |
|------|-----|------|
| `priority` | `normal` | 进程优先级保持正常 |
| `parallel_workers` | 2 | 2 个并发视频处理 |
| `cpu_threads_per_worker` | `cpu_count() - 1` → 约 5/worker | 满负荷 ASR |
| `video_cooldown_seconds` | 0 | 无冷却 |
| 触发条件 | 用户 ≥ 5 分钟无键盘/鼠标操作 | |

#### Profile "active"（桌面活跃模式）
| 参数 | 值 | 说明 |
|------|-----|------|
| `priority` | `below_normal` | 降低进程优先级 |
| `parallel_workers` | 1 | 单视频串行 |
| `cpu_threads_per_worker` | 2 | 最少 CPU 占用 |
| `video_cooldown_seconds` | 10 | 视频间执行冷却 |
| 触发条件 | 用户 ≤ 5 分钟内有过操作 | |

#### Scenario R1.1: 启动后默认状态
- **WHEN** 程序启动
- **THEN** 初始状态为 `idle`（满负荷），等待第一个 idle 检测周期确认后生效
- **AND** 日志输出 "性能 Profile: idle — 满负荷模式"

#### Scenario R1.2: profile 配置可通过 CLI 覆盖
- **WHEN** 用户指定 `--profile auto`（默认）
- **THEN** 启用自动检测与切换
- **WHEN** 用户指定 `--profile active`
- **THEN** 始终保持在 active 模式，不检测桌面空闲
- **WHEN** 用户指定 `--profile idle`
- **THEN** 始终保持在 idle 模式，不检测桌面空闲

---

### Requirement R2: 桌面空闲检测

系统 SHALL 通过 Windows `GetLastInputInfo` API 检测用户最后一次键盘/鼠标输入的时间，判断 desktop 是否活跃。

#### Scenario R2.1: 检测到桌面活跃
- **GIVEN** 当前 profile 为 `idle`
- **WHEN** `GetLastInputInfo` 返回值指示最后一次输入 < 5 分钟前
- **THEN** profile 切换为 `active`
- **AND** 调用 `set_low_priority("below_normal")`
- **AND** 调整 `parallel_workers=1`, `cpu_threads=2`
- **AND** 日志输出 "性能 Profile 切换: idle → active (检测到桌面活跃)"

#### Scenario R2.2: 检测到桌面闲置
- **GIVEN** 当前 profile 为 `active`
- **WHEN** `GetLastInputInfo` 返回值指示最后一次输入 ≥ 5 分钟前，且连续 2 次检测周期均确认闲置
- **THEN** profile 切换为 `idle`
- **AND** 调用 `set_low_priority("normal")`
- **AND** 调整 `parallel_workers=2`, `cpu_threads` 恢复满负荷
- **AND** 日志输出 "性能 Profile 切换: active → idle (桌面闲置 ≥ 5 分钟)"

#### Scenario R2.3: 非 Windows 平台降级
- **GIVEN** 程序运行在非 Windows 平台
- **WHEN** 无法调用 `GetLastInputInfo`
- **THEN** 自动降级为固定 `idle` profile（满负荷）
- **AND** 日志输出 "桌面空闲检测不可用（非 Windows 平台），固定为 idle profile"

#### Scenario R2.4: 防抖 — 避免频繁切换
- **GIVEN** profile 从 `idle` 切换到 `active`
- **WHEN** 用户在切换后 30 秒内离开（idle）
- **THEN** 不立即切回 `idle`，需等待连续 2 次检测周期确认闲置
- **AND** 从 `active` → `idle` 需要 2 次连续闲置确认（即至少 2×检测间隔 时间）

---

### Requirement R3: Profile 状态机

系统 SHALL 实现一个有明确状态转换规则的性能管理状态机。

```
              ┌──────────────────────────────────┐
              │                                  │
              ▼                                  │
   ┌──────┐  用户活跃   ┌────────┐  连续2次闲置  │
   │ idle │ ──────────► │ active │ ──────────────┘
   └──────┘             └────────┘
       ▲                     │
       │    固定 idle         │ 固定 active
       │                     │
   ┌───┴─────┐          ┌───┴──────┐
   │ --profile│          │ --profile │
   │  idle   │          │  active   │
   └─────────┘          └──────────┘
```

#### Scenario R3.1: idle → active 转换
- **WHEN** 检测到桌面活跃（最后输入 < 5 分钟）
- **THEN** 立即执行转换：降低 priority、减少 worker、减少线程

#### Scenario R3.2: active → idle 转换
- **WHEN** 连续 2 次检测周期均确认桌面闲置（≥ 5 分钟无输入）
- **THEN** 执行转换：恢复 priority、增加 worker、恢复线程数

#### Scenario R3.3: 固定模式不受检测影响
- **GIVEN** `--profile active` 或 `--profile idle`
- **WHEN** 桌面状态变化
- **THEN** profile 保持不变，不执行任何切换

---

### Requirement R4: 动态参数应用

系统 SHALL 在 main 循环中定期检测 profile 变化，并将 profile 定义的运行参数应用到实际运行时行为中。

#### Scenario R4.1: 检测周期
- **GIVEN** 程序运行在持续模式
- **WHEN** 每批处理完成后（或最多每 60 秒）
- **THEN** 执行一次空闲检测与 profile 评估
- **AND** 如果 profile 发生变化，立即应用新参数

#### Scenario R4.2: 参数热切换
- **WHEN** profile 从 `idle` 切换到 `active`
- **THEN** 当前批次允许完成，下一批次使用 `active` 参数
- **AND** `cpu_threads` 调整在下一批 `process_batch` 调用前生效
- **AND** `parallel_workers` 调整在下一批 `process_batch` 调用前生效

#### Scenario R4.3: ASR 引擎线程数无需重启
- **WHEN** `cpu_threads` 参数变化
- **THEN** CTranslate2 的 `WhisperModel` 不需要重新加载（线程数在 `transcribe()` 调用时指定）
- **NOTE**: 如果实际测试发现需要重新加载，可降级为 profile 变化时重新调用 `ASREngine.prepare_model()`

---

## MODIFIED Requirements

### Requirement M1: `--priority` CLI 参数
原 `--priority` 参数在与 `--profile auto`（默认）同时使用时被 profile 覆盖。

#### Scenario M1.1: profile=auto 时 priority 由状态机管理
- **GIVEN** `--profile auto`（默认）
- **WHEN** profile 状态切换
- **THEN** 进程优先级由 profile 自动设置，忽略 `--priority` 参数值

#### Scenario M1.2: profile=active/idle 时 priority 由 profile 决定
- **GIVEN** `--profile active`
- **THEN** priority 固定为 `below_normal`
- **GIVEN** `--profile idle`
- **THEN** priority 固定为 `normal`

---

## Architecture Decisions

### AD1: 选择 5 分钟作为 idle 阈值
**理由**: Windows 默认屏保/锁屏时间为 5-10 分钟。5 分钟闲置足以区分"短暂离开"和"真正无人值守"。连续 2 次检测（共 10 分钟确认）进一步防止误切换。

### AD2: 检测周期为每批处理后或 60 秒
**理由**: 视频处理批次时间不可预测（30s~10min）。在批次间检测避免中断处理流程。60 秒上限确保即使长批处理中也能响应桌面状态变化。

### AD3: 使用 ctypes 调用 Windows API 而非 psutil
**理由**: 与现有 `throttle.py` 模式一致，不需要额外依赖。`GetLastInputInfo` 是 Windows User32 标准 API。

### AD4: 状态切换不中断正在处理的任务
**理由**: 中断正在执行的 `process_pipeline` 会导致 ASR/Agent 结果丢失，与 checkpoint-resume 的优雅退出设计冲突。新参数在下批次生效。
