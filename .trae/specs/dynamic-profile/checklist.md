# Checklist

- [x] `throttle.py` 包含 `get_user_idle_seconds()` 函数，返回秒数或 None
- [x] `throttle.py` 包含 `PerformanceProfile` 类，支持 IDLE/ACTIVE 两种状态
- [x] `active → idle` 切换需要连续 2 次闲置确认（防抖）
- [x] `idle → active` 切换为即时响应（不防抖）
- [x] 固定模式（`--profile active`/`--profile idle`）不响应桌面状态变化
- [x] `--profile` CLI 参数已添加到 `parse_args()`，默认值为 `auto`
- [x] `PipelineConfig` 包含 `profile: str = "auto"` 字段
- [x] 主循环在每批完成后执行 profile 评估
- [x] profile 切换时立即调用 `set_low_priority()` 变更进程优先级
- [x] profile 切换时更新 `cfg.parallel_workers` 和 `cfg.cpu_threads`
- [x] 非 Windows 平台降级为固定 `idle` profile
- [x] 非 Windows 平台下 `get_user_idle_seconds()` 返回 None 不崩溃
- [x] 所有现有测试（75 个）仍然通过
- [x] 新增 profile 单元测试（15 个）全部通过
- [x] 启动日志包含 "性能 Profile: [idle|active] — [满负荷|省电]模式"
- [x] profile 切换时日志包含 "性能 Profile 切换: [old] → [new] (原因)"
