# Daemon Singleton Enforcer Spec

## Why
之前的运行时出现了 3 个 daemon 实例同时运行的 bug。虽然已添加基本 PID 文件检查，但当前实现存在缺陷：(1) 未验证 PID 是否确属 VidBrain 进程 (2) 未处理 PID 文件残留的边界情况 (3) 未记录结构化告警日志。需要加固为可靠的单实例守护机制。

## What Changes
- `run_daemon.ps1`：重写 singleton 检查逻辑，增加进程名验证、PID 复用检测、过期 PID 文件清理、结构化日志告警

## Impact
- Affected specs: none
- Affected code: `run_daemon.ps1`

## ADDED Requirements

### Requirement: Daemon 单实例保证
系统 SHALL 在启动时通过 PID 文件机制确保同一时刻只有一个 daemon 实例运行。如果检测到已有实例，必须告警并退出。

#### Scenario: 首次启动
- **WHEN** `logs/daemon.pid` 不存在
- **THEN** 创建该文件写入当前进程 PID 并正常启动

#### Scenario: 重复启动 — 已有实例存活
- **WHEN** `logs/daemon.pid` 存在且指向的进程仍在运行，且该进程命令行包含 `run_daemon.ps1`
- **THEN** 打印告警信息到终端和 `logs/daemon.log`，包含已有实例 PID，以退出码 1 退出

#### Scenario: PID 文件残留 — 原进程已死
- **WHEN** `logs/daemon.pid` 存在但该 PID 对应的进程不存在，或该进程已退出
- **THEN** 删除残留 PID 文件，继续正常启动

#### Scenario: PID 复用 — PID 存在但不属于 VidBrain
- **WHEN** `logs/daemon.pid` 存在、进程存在、但该进程命令行不包含 `run_daemon.ps1`
- **THEN** 打印告警日志说明 PID 被复用，删除残留文件，继续正常启动

#### Scenario: 正常退出后清理
- **WHEN** daemon 因达到最大重启次数而退出
- **THEN** 删除 `logs/daemon.pid` 文件
