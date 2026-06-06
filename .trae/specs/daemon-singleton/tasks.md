# Tasks

## Task 1: 重写 daemon singleton 检查逻辑
- [x] 将 `run_daemon.ps1` 中的 singleton 检查替换为健壮的实现：
  - 检查 `logs/daemon.pid` 是否存在
  - 若存在：读取 PID，尝试 `Get-Process -Id $pid`，若进程不存在 → 删除残留文件，继续启动
  - 若进程存在：通过 WMI 获取该进程的 `CommandLine`，检查是否包含 `run_daemon.ps1`
  - 若不包含 → 告警"PID 被非 VidBrain 进程复用"，删除残留，继续启动
  - 若包含 → 告警"Daemon 已在运行 (PID: xxx)"，写入 `logs/daemon.log`，以 `exit 1` 退出
  - 格式统一：`[yyyy-MM-dd HH:mm:ss] WARN  ...` 写入 daemon.log
- [x] 确认 `exit 1` 用于重复启动情况

## Task 2: 验证 singleton 行为
- [x] 启动第一个 daemon，确认正常运行 → PASS (PID 4224)
- [x] 尝试启动第二个 daemon，确认告警并 exit 1 → PASS (exit=1, "already running" in log)
- [x] 杀掉第一个 daemon 后，确认可正常重新启动 → PASS (new PID 27424, old PID file cleaned)

# Task Dependencies
- 无依赖
