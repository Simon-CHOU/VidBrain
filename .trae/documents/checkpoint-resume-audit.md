# 断点续运行架构审查与修复计划

## 问题诊断

经逐行审查 `main.py`、`pipeline.py`、`db.py`、`throttle.py`、`singleton.py` 的状态机和信号处理逻辑，发现以下关键缺陷。

---

## 1. 状态机分析

### 当前状态流转

```
PENDING ─┬→ ASR_PROCESSING ─┬→ (成功) ASR_DONE ─→ AGENT_PROCESSING ─┬→ SUCCESS
         │                   │                                        ├→ DRAFT_PENDING
         │                   │                                        └→ PERMANENTLY_FAILED (≥3次)
         │                   └→ (异常) ─→ PENDING (retry<3) 或 PERMANENTLY_FAILED
         │
         └→ SKIP (非 tech 类)
```

### 关键发现：每个阶段的 DB 写入时机

| 阶段 | ps_ing 写入 | 结果写入 | 崩溃后丢失 |
|------|------------|---------|-----------|
| ASR | L108: `ASR_PROCESSING` | L116: `ASR_DONE` + `raw_asr_json` | **全部 ASR 结果**（因为 DB 写入在转录完成后） |
| Agent | L122: `AGENT_PROCESSING` | L167: Agent 结果在内存 `final_state` 中，**未写入 DB** | **全部 Agent 结果** |
| Write | 无 | L199-205: 先写文件，后 L205: `SUCCESS` | 文件可能半写完，DB **未标记成功** |

---

## 2. 缺陷清单

### D1 [严重] 启动时无中间状态恢复

**位置**: `main.py` L533-548 (启动流程)

**现状**: `main()` 启动后直接进入 `process_batch()`，不处理任何中间状态（`ASR_PROCESSING`/`AGENT_PROCESSING`）。

**后果**: 异常退出后，卡在中间状态的任务永远无法被拾取。`process_batch()` 只查询 `status='PENDING' AND category='tech'`，中间状态被跳过。用户每次重启都必须手动运行重置脚本。

**证据**: 我们之前每次 kill 进程后都需要 `_reset.py` 来 `UPDATE ... SET status='PENDING' WHERE status IN ('ASR_PROCESSING', 'AGENT_PROCESSING')`。

### D2 [严重] 信号处理器不安全

**位置**: `main.py` L715-729

```python
def shutdown(signum, frame):
    ...
    executor.shutdown(wait=False)   # 立即杀线程，不等待
    ...
    sys.exit(0)                      # 在 signal context 中调用 sys.exit 不安全
```

**问题**:
- `executor.shutdown(wait=False)` 不等待正在执行的 `process_pipeline()` 完成。正在转录的视频 ASR 结果全部丢失，线程在不可预知点被杀死。
- `sys.exit(0)` 在信号处理器中调用可能导致死锁或异常（Python 文档明确警告不要在 signal handler 中做重操作）。
- 无任何状态清理：正在处理的视频停留在 `ASR_PROCESSING` 或 `AGENT_PROCESSING`。

### D3 [严重] `get_failed_retryable()` 是死代码

**位置**: `db.py` L236-244, `pipeline.py` L246-254

**现状**: `get_failed_retryable()` 查询 `status='FAILED'`，但错误处理（L246-261）直接将状态设为 `PENDING` 或 `PERMANENTLY_FAILED`，从未设为 `FAILED`。

```python
# pipeline.py L246-261
retry_count = db.increment_retry(video_id, error_msg)
if retry_count >= 3:
    db.update_status(video_id, "PERMANENTLY_FAILED", error_msg=error_msg)  # 永不自动重试
else:
    db.update_status(video_id, "PENDING")  # 立即可重试
```

**后果**: `process_batch()` 中的自动重试循环（L270-280）永远不会生效，`get_failed_retryable()` 永远返回空。

### D4 [中等] 输出文件非原子写入

**位置**: `pipeline.py` L199

```python
output_path.write_text(full_content, encoding="utf-8")
db.update_status(video_id, "SUCCESS")
```

**问题**: 先写文件，后更新 DB。如果写文件过程中崩溃→文件损坏但 DB 无记录。如果写文件成功但 DB 更新前崩溃→文件存在但 DB 状态不是 SUCCESS → 重启后可能重复处理（虽然概率低，因为状态回到 PENDING 后会被重新 ASR，但文件已存在会导致重复笔记）。

### D5 [中等] Agent 结果未持久化

**位置**: `pipeline.py` L156-170

Agent 处理三步 LLM 调用（clean→link→suggest），结果仅存在于 `final_state` 内存字典中。任何崩溃（包括系统崩溃、OOM、电源故障）都会丢失已消耗的 LLM Token。

### D6 [低] ASR 增量结果未保存

**位置**: `pipeline.py` L112

`asr_engine.transcribe()` 返回完整的 `list[dict]`，只有全部完成后才写入 DB（L116）。对于长视频（>30min），如果转录过程中崩溃，浪费大量 CPU 时间。

---

## 3. 修复方案

### F1 [优先] 启动时自动恢复中间状态

**文件**: `vidbrain/main.py`（新增函数）或 `vidbrain/db.py`（新增方法）

在 `init_db()` 后、`process_batch()` 前，增加恢复步骤：

```python
# db.py 新增方法
def recover_stuck_tasks(self) -> int:
    """将卡在中间状态的任务恢复为 PENDING。"""
    with self._lock:
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute(
                "UPDATE video_pipeline SET status='PENDING' "
                "WHERE status IN ('ASR_PROCESSING', 'AGENT_PROCESSING')"
            )
            conn.commit()
            return cursor.rowcount
```

```python
# main.py L547 后新增
recovered = db.recover_stuck_tasks()
if recovered > 0:
    logger.warning("从异常中断恢复: 重置了 %d 个卡住的任务为 PENDING", recovered)
```

**收益**: 消除手动重置脚本，实现真正的"断点续运行"。

### F2 [优先] 安全信号处理

**文件**: `vidbrain/main.py` L715-729

重构 `shutdown()` 为安全实现：

```python
_shutdown_requested = False

def request_shutdown(signum, frame):
    """信号处理器：只设置标志位，不做任何重操作。"""
    global _shutdown_requested
    logger.info("收到关闭信号 (signal=%d)，等待当前任务完成后退出...", signum)
    _shutdown_requested = True

signal.signal(signal.SIGINT, request_shutdown)
signal.signal(signal.SIGTERM, request_shutdown)
```

在主循环中检查标志位：

```python
while True:
    if _shutdown_requested:
        logger.info("执行优雅退出...")
        break
    time.sleep(cfg.interval_seconds)
    ...
```

退出时代码：

```python
# 优雅退出
logger.info("等待正在进行的任务完成...")
executor.shutdown(wait=True, timeout=300)  # 最多等 5 分钟
observer.stop()
observer.join()
metrics.log_summary()
metrics.flush_to_db()
logger.info("VidBrain 已优雅停止")
```

**收益**: 当前正在转录的视频不会半途而废，其结果会被保存到 DB；信号处理安全不引发死锁。

### F3 [优先] 修复 `get_failed_retryable()` 死代码

**文件**: `vidbrain/pipeline.py` 或可选择简化为直接使用 PENDING 机制

**两种方案**:

方案 A（推荐）：简化 - 删除 `get_failed_retryable()` 和 `process_batch()` 中对应的重试循环（L270-280）。错误处理已经直接将任务重置为 PENDING，下次 `process_batch()` 会自然重试。

方案 B：保留独立的 FAILED 状态 - 将错误处理改为 `db.update_status(video_id, "FAILED", error_msg=error_msg)`，让 `get_failed_retryable()` 正常工作。

推荐方案 A，因为功能已由 PENDING 机制覆盖，减少复杂度。

### F4 [中等] 输出文件原子写入

**文件**: `vidbrain/pipeline.py` L198-205

改为先写临时文件再重命名：

```python
import tempfile, shutil

fd, tmp_path = tempfile.mkstemp(dir=str(vault_path), suffix=".md", prefix=".vidbrain_tmp_")
os.close(fd)
try:
    Path(tmp_path).write_text(full_content, encoding="utf-8")
    shutil.move(tmp_path, output_path)  # 原子操作（同磁盘）
    db.update_status(video_id, "SUCCESS")
except Exception:
    try:
        os.unlink(tmp_path)
    except OSError:
        pass
    raise
```

### F5 [中等] Agent 结果持久化

**文件**: `vidbrain/pipeline.py`, `vidbrain/db.py`

在 Agent 完成后、写入文件前，将 `final_markdown` 写入 DB 的 `raw_asr_json` 字段（重用该字段），或将 `raw_asr_json` 和 `final_markdown` 分开存储：

```python
# pipeline.py Agent 完成后
final_state = graph.invoke(initial_state)
# 持久化 Agent 输出（使用 raw_asr_json 存储完整管线数据）
db.update_status(video_id, "AGENT_DONE", raw_asr=json.dumps({
    "asr_segments": asr_data,
    "final_markdown": final_state["final_markdown"],
}, ensure_ascii=False))
db.update_status(video_id, "AGENT_PROCESSING")  # 实际的 Agent 完成标记
```

注意：这需要增加 `AGENT_DONE` 状态，或修改现有的状态流转来区分 "ASR 完成"和 "Agent 完成"。

简化方案：不新增状态，直接复用 `raw_asr_json` 字段，在 Agent 完成后覆盖写入完整数据。

### F6 [低] ASR 增量保存（可选）

**文件**: `vidbrain/asr_engine.py`

faster-whisper 的 `transcribe()` 返回生成器，每段转录可以增量收集。在 pipeline 中增加定期 DB 写入：

```python
# 伪代码
for i, segment in enumerate(segments):
    results.append({...})
    if i % 20 == 0:  # 每 20 段保存一次
        db.update_asr_partial(video_id, results)
```

**注意**: 这需要新增 DB 字段 `partial_asr_json` 或修改现有 schema。收益有限（只有极长视频会崩溃时有用），建议延后实现。

---

## 4. 实施优先级

| 优先级 | 编号 | 修复项 | 影响 |
|--------|------|--------|------|
| **P0** | F1 | 启动时自动恢复中间状态 | 消除手动重置，实现断点续运行的核心 |
| **P0** | F2 | 安全信号处理 | 当前 KILL 进程不会丢失正在执行的 ASR |
| **P0** | F3 | 修复死代码 | 清理无用的 `get_failed_retryable()` |
| **P1** | F4 | 原子文件写入 | 防止输出文件损坏 |
| **P1** | F5 | Agent 结果持久化 | 避免 Agent 崩溃时浪费 LLM Token |
| **P2** | F6 | ASR 增量保存 | 长视频场景下的额外保障 |

---

## 5. 验证方法

1. **F1 验证**: 正常启动，确认日志中有 "恢复: 重置了 0 个卡住的任务"；KILL 进程后重启，确认中间状态任务被重置为 PENDING 并继续处理
2. **F2 验证**: 按 Ctrl+C，确认日志显示 "等待当前任务完成后退出" 而非立即终止；确认当前视频的 ASR 结果被保存到 DB
3. **F3 验证**: 运行 `python -c "from vidbrain.db import DatabaseManager; db=DatabaseManager('./pipeline.db'); print(len(db.get_failed_retryable()))"` 确认返回 0（且代码简化后无副作用）
4. **F4 验证**: 处理视频，确认输出文件正常写入；检查 vault 中无残留 `.vidbrain_tmp_*` 文件
5. **F5 验证**: Agent 完成后检查 DB 中 `raw_asr_json` 包含 `final_markdown`；模拟崩溃后重启确认无需重新 Agent 处理
