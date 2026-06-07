# Continuous Streaming Pipeline Plan

## Summary

将当前的 cron-like 批量调度模式（`sleep(30min) → process_batch(6) → repeat`）改为真正流式的持续处理模式（处理完一个视频立即取下一个，不停歇），更贴合"流水线"语义。

## Current State

### 当前主循环

```
while True:
    time.sleep(cfg.interval_seconds)         # 30分钟死等
    classify_all_pending()                    # 扫描新文件
    process_batch(cfg.batch_size)             # 一次取N个，全部处理完
    profile_evaluate()                        # 批次后才检查
    metrics / refine                          # 批次后才做
```

**问题**:
1. `time.sleep(30min)` 导致大批空闲窗口 — 用户感知"停了"
2. `process_batch(6)` 把所有视频捆在一起 — 不能细粒度感知 profile 变化
3. profile 切换只在批次间生效 — 一个 6 视频批次可能跑 10+ 分钟，期间无法响应桌面状态变化
4. 整体行为像 cron job，不像 pipeline

### 相关文件

| 文件 | 相关代码 |
|------|---------|
| `config.py:74-75` | `batch_size: int = 10`, `interval_seconds: int = 0` |
| `main.py:85-92` | `--interval`, `--batch-size`, `--once` CLI args |
| `main.py:270-334` | `process_batch()` — 批量取N个任务并处理 |
| `main.py:753-810` | 主循环 while True 逻辑 |

---

## Proposed Changes

### 1. 新增 `--continuous` 模式

**文件**: `main.py` CLI, `config.py`

```python
# config.py
continuous: bool = False  # 流式持续处理（不停歇，一个接一个）

# main.py CLI
parser.add_argument("--continuous", action="store_true",
    help="流式持续处理：处理完一个视频立即取下一个，不等待间隔")
```

**行为**:
- `--continuous` 启用时: 单视频循环，无间隔等待
- `--interval 30m` 保留现有 cron-like 行为（向后兼容）
- `--once` 处理一批后退出（向后兼容）
- 默认行为不变（`--interval 30m` 仍为默认）

### 2. 重写主循环为两条路径

**文件**: `main.py` L707-810

#### 路径 A: 流式模式 (`--continuous`)

```
while True:
    shutdown_check()
    
    # 取一个待处理任务
    tasks = db.get_pending_tech_tasks(limit=1)
    if not tasks:
        # 无任务时短暂等待，然后重新扫描
        classify_all_pending()
        time.sleep(30)  # 30秒后重试
        continue
    
    # 处理单个视频
    task = tasks[0]
    process_pipeline(task["id"], task["video_name"], task["file_path"], ...)
    
    # 每个视频完成后：评估 profile → 可能热切换参数
    profile_evaluate_and_apply()
    
    # 定期指标落盘
    metrics_flush_if_needed()
    
    # 定期自动精炼
    auto_refine_if_needed()
```

**关键特性**:
- 无间隔等待（除无任务时的 30s 重试）
- 每个视频完成后立即评估 profile（细粒度响应桌面状态）
- profile 参数（parallel_workers, cpu_threads）在下个视频生效
- `--limit` 仍然生效，达到上限后退出

#### 路径 B: cron-like 模式 (`--interval`, 现有行为保持)

保持现有逻辑不变，确保向后兼容。

### 3. 保留 `process_batch()` 不变

`process_batch()` 仍用于 `--once` 模式和 cron-like 模式，不做修改。流式模式在循环内直接调用 `process_pipeline()` 单个任务。

### 4. 简化 `--profile auto` 的参数应用时机

**文件**: `main.py` L588-597

将 profile 参数初始化和热切换统一为一个 helper:

```python
def _apply_profile_params(cfg, perf_profile):
    params = perf_profile.get_params()
    cfg.parallel_workers = params["parallel_workers"]
    cfg.cpu_threads = params["cpu_threads_per_worker"]
    cfg.video_cooldown = params["video_cooldown_seconds"]
```

启动时和切换时均调用此 helper。

### 5. profile 评估改为"每次视频前"而非"每批后"

**文件**: `main.py` L770-780

流式模式下，profile 评估从"每批后"移到"每个视频前"：
- `perf_profile.evaluate()` 内部已有 60s 检测间隔限制，不会过度频繁调用 Windows API
- 用户开始操作 desktop → 当前视频完成后立即切换到 active（最多延迟一个视频的时间）
- 用户离开 → 连续2次闲置确认后切换到 idle

---

## Architecture Decisions

### AD1: 不删除 `--interval` / `process_batch`
向后兼容，现有用户脚本不破坏。流式模式通过新 `--continuous` 标志启用。

### AD2: 无任务时 30s 重试而非事件驱动
`watchdog` 已监听新文件，但 watchdog 提交任务到 executor 而非通知主循环。改为事件驱动需要更大重构。30s 轮询开销极低（一次 SQL 查询），且能与 watchdog 互补。

### AD3: 单视频处理而非小批量
`process_batch(limit=1)` 等价于单视频处理，但语义更清晰：直接 `get_pending_tech_tasks(limit=1)` + `process_pipeline()`。去掉多余的 batch 抽象。

---

## Files Changed

| 文件 | 变更 |
|------|------|
| `config.py` | 新增 `continuous: bool = False` |
| `main.py` | 新增 `--continuous` CLI; 新增流式主循环分支; 提取 `_apply_profile_params()` helper; profile 评估移到每个视频间 |

## Verification

1. `--continuous` 模式下处理完一个视频后立即开始下一个（无延迟）
2. 无任务时日志显示 "无待处理任务，30s 后重试"
3. 在视频处理期间操作桌面 → 当前视频完成后 profile 切换为 active
4. `--once` 模式行为不变
5. `--interval 30m` 模式行为不变
6. 90 个现有测试全部通过
