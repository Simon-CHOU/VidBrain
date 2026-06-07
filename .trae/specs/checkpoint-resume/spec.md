# Checkpoint-Resume Resilience Spec

## Why
程序在任何退出方式（主动 Ctrl+C、系统关机、进程异常崩溃、OOM Kill）后重新启动时，应能从上次中断点继续处理，而非从头开始或卡死在中间状态。当前实现存在三个关键缺陷：(1) 启动时未恢复卡在中间状态（ASR_PROCESSING/AGENT_PROCESSING）的任务，导致永久卡死；(2) 信号处理器直接调用 `executor.shutdown(wait=False)` 和 `sys.exit(0)`，不安全且丢失正在进行的 ASR 结果；(3) `get_failed_retryable()` 查询 `status='FAILED'`，但代码中从未设置该状态，为重试死代码。此外，输出文件写入、Agent 结果均未做持久化保护。

## What Changes

### 核心变更
- **db.py**: 新增 `recover_stuck_tasks()` 方法，将中间状态任务重置为 PENDING；删除 `get_failed_retryable()` 死代码
- **main.py**: 启动流程增加自动恢复步骤；重构 `shutdown()` 信号处理器为 flag-based 优雅退出模式；退出前等待正在执行的任务完成

### 可靠性增强
- **pipeline.py**: 输出文件改为临时文件+rename 原子写入；Agent 完成后将 `final_markdown` 与 `raw_asr_json` 合并持久化到 DB

### 清理
- **main.py / db.py**: 移除 `get_failed_retryable()` 及 `process_batch()` 中对应的无效重试循环

## Impact
- Affected specs: daemon-singleton（退出流程变更需兼容）
- Affected code: `vidbrain/db.py`, `vidbrain/main.py`, `vidbrain/pipeline.py`

---

## ADDED Requirements

### Requirement R1: 启动时自动恢复中间状态
系统 SHALL 在数据库初始化后、开始处理任务前，自动将所有卡在中间状态（ASR_PROCESSING、AGENT_PROCESSING）的任务重置为 PENDING。

#### Scenario R1.1: 正常首次启动
- **GIVEN** 数据库刚初始化，所有任务状态为 PENDING
- **WHEN** `main()` 调用 `db.recover_stuck_tasks()`
- **THEN** 返回 0（无卡住任务），日志输出 "启动恢复检查: 无卡住任务"

#### Scenario R1.2: 异常中断后重启
- **GIVEN** 数据库中有 2 个任务状态为 ASR_PROCESSING，1 个为 AGENT_PROCESSING
- **WHEN** `main()` 调用 `db.recover_stuck_tasks()`
- **THEN** 3 个任务均被重置为 PENDING，日志输出 "从异常中断恢复: 重置了 3 个卡住的任务为 PENDING"
- **AND** 后续 `process_batch()` 正常拾取并重新处理这些任务

#### Scenario R1.3: 已永久失败的任务不受影响
- **GIVEN** 数据库中有 1 个状态为 PERMANENTLY_FAILED 的任务
- **WHEN** `main()` 调用 `db.recover_stuck_tasks()`
- **THEN** 该 PERMANENTLY_FAILED 任务保持原状态不变
- **AND** 返回的计数不包含该任务

#### Scenario R1.4: SUCCESS 任务不受影响
- **GIVEN** 数据库中有 256 个状态为 SUCCESS 的任务
- **WHEN** `main()` 调用 `db.recover_stuck_tasks()`
- **THEN** 所有 SUCCESS 任务保持原状态不变

#### Scenario R1.5: 恢复操作集成到启动时序
- **GIVEN** 程序启动
- **WHEN** 执行流程到达 `db.init_db()` 之后
- **THEN** `db.recover_stuck_tasks()` 必须在 `process_batch()` 之前执行
- **AND** 恢复操作必须在单例锁获取之后，避免多实例同时恢复

---

### Requirement R2: 安全信号处理与优雅退出
系统 SHALL 使用安全的 flag-based 信号处理机制。收到 SIGINT/SIGTERM 时，信号处理器仅设置原子标志位，主循环检测到标志位后执行优雅退出：停止接收新任务、等待正在执行的任务完成（最多 5 分钟超时）、保存指标和审计日志、释放单例锁。

#### Scenario R2.1: 收到 SIGINT（Ctrl+C）
- **GIVEN** 程序正在处理一批视频，其中 2 个视频正在 ASR 转录
- **WHEN** 用户按 Ctrl+C
- **THEN** 信号处理器立即设置 `_shutdown_requested = True` 并返回
- **AND** 主循环在完成当前 `process_batch()` 后检测到标志位
- **AND** 调用 `executor.shutdown(wait=True, timeout=300)`
- **AND** 2 个正在转录的视频完成 ASR 并将结果写入 DB（raw_asr_json）
- **AND** 日志输出 "等待正在进行中的任务完成..."
- **AND** 指标和审计日志正确落盘
- **AND** 单例 PID 文件被 atexit 清理
- **AND** 程序以退出码 0 结束

#### Scenario R2.2: 等待超时强制退出
- **GIVEN** 程序收到 SIGINT，但某个正在执行的任务卡住（如 LLM API 无响应）
- **WHEN** `executor.shutdown(wait=True, timeout=300)` 等待超过 300 秒
- **THEN** 强制退出，日志输出 "等待任务完成超时，强制退出"
- **AND** 卡住的任务状态可能为 ASR_PROCESSING 或 AGENT_PROCESSING
- **AND** 下次重启时 R1.2 会将其恢复为 PENDING

#### Scenario R2.3: 空闲时收到信号
- **GIVEN** 程序处于持续模式，当前在 `time.sleep(interval_seconds)` 中等待
- **WHEN** 收到 SIGINT
- **THEN** `time.sleep()` 被中断，主循环检测到 `_shutdown_requested` 并退出
- **AND** 无正在执行的任务需要等待
- **AND** 正常清理退出

#### Scenario R2.4: 信号处理器不执行重操作
- **GIVEN** 定义信号处理函数 `request_shutdown(signum, frame)`
- **WHEN** 审查其代码
- **THEN** 函数体中 **不得** 包含以下操作：
  - `sys.exit()`
  - 文件 I/O（`open`、`write`、`dump_json`）
  - 网络调用
  - 锁获取（`threading.Lock.acquire`）
  - 任何可能阻塞的操作
- **AND** 函数体仅包含：设置全局标志位 + 一条日志输出

#### Scenario R2.5: 多次信号防抖
- **GIVEN** 已收到一次 SIGINT，正在等待任务完成
- **WHEN** 用户再次按 Ctrl+C
- **THEN** 第二次信号被忽略（标志位已设置），不触发额外行为

---

### Requirement R3: 清理无用重试代码
系统 SHALL 移除 `get_failed_retryable()` 方法及 `process_batch()` 中对应的自动重试循环，因为重试逻辑已由"异常→重置为 PENDING→下批次自然重试"的机制覆盖。

#### Scenario R3.1: 异常发生时的重试路径
- **GIVEN** 一个任务在 Agent 处理阶段发生异常（retry_count = 0）
- **WHEN** `process_pipeline()` 捕获异常
- **THEN** `db.increment_retry()` 将 retry_count 增为 1
- **AND** retry_count < 3 → `db.update_status(video_id, "PENDING")`
- **AND** 下次 `process_batch()` 查询 PENDING 任务时自然拾取
- **AND** 不依赖 `get_failed_retryable()` 或 FAILED 状态

#### Scenario R3.2: 重试 3 次后永久失败
- **GIVEN** 一个任务已失败 2 次（retry_count = 2）
- **WHEN** 第 3 次执行再次失败
- **THEN** `db.increment_retry()` 将 retry_count 增为 3
- **AND** retry_count >= 3 → `db.update_status(video_id, "PERMANENTLY_FAILED")`
- **AND** 该任务不再自动重试，等待手动 `--retry-failed` 干预

#### Scenario R3.3: 删除后无副作用
- **GIVEN** 已删除 `get_failed_retryable()` 和 `process_batch()` 中的重试循环
- **WHEN** 运行全部单元测试
- **THEN** 75 个测试全部通过（当前无针对 FAILED 状态的测试）
- **AND** `process_batch()` 仍能正确处理 PENDING 任务的重试

---

### Requirement R4: 输出文件原子写入
系统 SHALL 使用"临时文件写入 + rename"方式原子化地写入 Obsidian 笔记文件，确保不会产生损坏的半截文件，且 vault 中不会遗留临时文件。

#### Scenario R4.1: 正常写入
- **GIVEN** Agent 处理完成，`final_markdown` 已就绪
- **WHEN** `process_pipeline()` 进入写入阶段
- **THEN** 先创建临时文件 `.vidbrain_tmp_XXXX.md` 在同一 vault 目录下
- **AND** 将完整内容（front-matter + markdown）写入临时文件
- **AND** `os.rename()` / `shutil.move()` 将临时文件原子重命名为目标路径
- **AND** 重命名成功后 `db.update_status(video_id, "SUCCESS")`
- **AND** 临时文件不存在于 vault 中

#### Scenario R4.2: 写入过程中崩溃
- **GIVEN** 正在向临时文件写入内容
- **WHEN** 进程被 Kill 或系统断电
- **THEN** 目标文件不存在（未重命名）
- **AND** vault 中可能残留 `.vidbrain_tmp_` 临时文件
- **AND** DB 状态保持为 AGENT_PROCESSING（非 SUCCESS）
- **AND** 下次启动时 R1.2 将任务恢复为 PENDING → 重新处理

#### Scenario R4.3: 临时文件残留清理
- **GIVEN** 上次崩溃遗留了 `.vidbrain_tmp_*.md` 临时文件
- **WHEN** `process_pipeline()` 下一次写入前或程序启动时
- **THEN** 可选：检测并清理残留临时文件（至少不影响正常流程）
- **NOTE**: 此为增强可选行为，核心要求是 vault 中不能有半截的目标文件

---

### Requirement R5: Agent 结果持久化
系统 SHALL 在 Agent 三步 LLM 处理完成后、写入 Obsidian 文件前，将 `final_markdown` 与 `raw_asr_json` 合并保存到 DB，确保即使随后崩溃也不丢失已消耗 Token 生成的 Agent 输出。

#### Scenario R5.1: Agent 完成后持久化
- **GIVEN** Agent 处理完成，`final_state["final_markdown"]` 非空
- **WHEN** `process_pipeline()` 执行到 Agent 完成后的持久化步骤
- **THEN** 将 ASR 结果和 Agent 输出合并写入 `raw_asr_json` 字段：
  ```json
  {"asr_segments": [...], "final_markdown": "..."}
  ```
- **AND** 日志输出 "Agent 结果已持久化"
- **AND** 随后继续执行文件写入步骤

#### Scenario R5.2: 崩溃恢复时复用 Agent 结果
- **GIVEN** 一个任务上次处理到 Agent 完成并已持久化，但随后崩溃未写入文件
- **WHEN** 下次启动恢复后重新处理（状态恢复为 PENDING → 重新进入 pipeline）
- **THEN** 系统检查 `raw_asr_json` 中是否包含 `final_markdown`
- **AND** 如果存在 → **跳过 ASR 和 Agent 阶段**，直接进入 Step 4 写入文件
- **AND** 日志输出 "检测到已持久化的 Agent 结果，跳过重新处理"
- **NOTE**: 此恢复路径为进阶优化，初次实现可先做持久化，恢复路径在后续迭代补充

#### Scenario R5.3: 向后兼容旧的 raw_asr_json 格式
- **GIVEN** 数据库中 `raw_asr_json` 为旧格式（纯 ASR segments 列表）
- **WHEN** Agent 完成后写入新的合并格式
- **THEN** 新格式覆盖旧格式
- **AND** 读取时通过检测 `{"asr_segments": ...}` 结构区分新旧格式

---

## MODIFIED Requirements

### Requirement M1: 进程退出流程变更
原 `shutdown(signum, frame)` 函数必须被替换为基于标志位的优雅退出流程。

#### Scenario M1.1: 单次模式正常退出
- **GIVEN** 程序以 `--once` 模式运行
- **WHEN** `process_batch()` 返回后
- **THEN** 原流程不变：日志 "单次模式完成" + 正常返回
- **AND** `main()` 返回 → atexit 触发 → PID 文件清理

#### Scenario M1.2: 持续模式主动退出
- **GIVEN** 持续模式运行中，收到 SIGINT
- **WHEN** 执行优雅退出流程
- **THEN** 与信号处理相同的退出路径：
  - `executor.shutdown(wait=True, timeout=300)`
  - `observer.stop()` + `observer.join()`
  - 指标落盘
  - 审计日志写入 "shutdown" 事件
- **AND** 退出码 0

---

## Architecture Decisions

### AD1: 选择 flag-based 信号处理
**理由**: Python 的 `signal` 模块文档明确警告信号处理器中不应做重操作。`sys.exit()` 在信号上下文中可能触发死锁。使用原子布尔标志位是最简单安全的方式。

### AD2: executor.shutdown(wait=True, timeout=300)
**理由**: `wait=True` 等待线程池中的任务自然完成。300 秒超时覆盖大多数视频的 ASR 耗时（即使是长视频，5 分钟的超时也足够安全）。超时后强制退出，任务状态通过 R1 在下一次启动时恢复。

### AD3: 选择 tempfile + rename 原子写入
**理由**: `write_text()` + `db.update_status()` 两步操作非原子。rename 是文件系统原子操作（同磁盘），确保目标文件要么完整要么不存在。不引入外部依赖（tempfile 和 shutil 均为标准库）。

### AD4: 使用 raw_asr_json 字段存储合并数据
**理由**: 避免修改 SQLite schema（ALTER TABLE 在并发下不安全）。JSON 格式自然支持结构变更。通过 key 检测区分新旧格式。

### AD5: 删除而非修复 get_failed_retryable()
**理由**: 该函数查询 `status='FAILED'`，但代码从未设置此状态。错误处理直接重置为 PENDING，功能已由 PENDING → process_batch 自动覆盖。保留死代码增加维护成本。
