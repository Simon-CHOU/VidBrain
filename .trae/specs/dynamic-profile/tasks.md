# Tasks

## Task 1: 实现桌面空闲检测模块
- [x] 在 `vidbrain/throttle.py` 中新增 `get_user_idle_seconds()` 函数
  - 通过 `ctypes.windll.user32.GetLastInputInfo` 获取用户最后一次输入的时间
  - 返回距离最后一次输入的秒数
  - 非 Windows 平台返回 `None`（表示不可用）
- [x] 新增 `USER_IDLE_THRESHOLD = 300`（5 分钟）常量

## Task 2: 实现 PerformanceProfile 状态机
- [x] 在 `vidbrain/throttle.py` 中新增 `PerformanceProfile` 类
  - 定义 `Profile.IDLE` 和 `Profile.ACTIVE` 枚举
  - 定义每个 profile 的参数映射：`priority`, `parallel_workers`, `cpu_threads_per_worker`, `video_cooldown_seconds`
  - 实现 `evaluate(current_profile, idle_seconds)` 方法：根据空闲时长和连续闲置计数返回新的 profile
  - 实现防抖：active → idle 需要连续 2 次闲置确认（`_consecutive_idle_checks` 计数器）
  - 实现 `apply(profile)` 方法：调用 `set_low_priority()` 应用进程优先级
  - 日志输出每次状态切换
- [x] 新增 `--profile` CLI 参数到 `main.py` 的 `parse_args()`:
  - 选项：`auto`（默认）, `active`, `idle`
  - `auto` 启用自动检测和切换
  - `active`/`idle` 固定模式，不检测桌面状态
- [x] 在 `config.py` 的 `PipelineConfig` 中新增 `profile: str = "auto"`

## Task 3: 集成到主循环
- [x] 在 `main.py` 主循环中集成 profile 评估
  - 每批 `process_batch()` 完成后调用 `profile.evaluate()`
  - 如果 profile 发生变化，调用 `profile.apply()` 并更新 `cfg` 运行参数
- [x] 确定检测时机
  - 持续模式：每批处理完成后检测（evaluate 内部有 60s 间隔限制）
  - 单次模式：启动时通过 PerformanceProfile 构造器设置初始状态

## Task 4: 更新文档与测试
- [x] `--priority` 帮助文本保留，profile 在 auto 模式管理优先级
- [x] 新增单元测试 `vidbrain/tests/test_profile.py` (15 个测试用例)：
  - `test_returns_int_or_none`
  - `test_fixed_idle_no_switch`
  - `test_fixed_active_no_switch`
  - `test_auto_starts_idle`
  - `test_get_params_returns_expected_keys`
  - `test_get_params_for_specific_profile`
  - `test_is_idle_active`
  - `test_evaluate_idle_to_active`
  - `test_evaluate_active_to_idle_requires_debounce`
  - `test_evaluate_respects_detection_interval`
  - `test_non_windows_fallback`
  - `test_apply_calls_set_low_priority`
  - `test_set_normal_is_noop`
  - `test_set_invalid_level`

# Task Dependencies
- Task 2 depends on Task 1
- Task 3 depends on Task 2
- Task 4 depends on Task 3
