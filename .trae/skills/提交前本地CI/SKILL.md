---
name: 提交前本地CI
description: 在修复问题、准备提交 commit、推送或开 PR 前运行本地 lint-and-test，并在失败时先修复再继续。
---

# 提交前本地 CI

当任务涉及代码修改、缺陷修复、准备提交 commit、推送分支、创建 PR，或用户明确提到 CI / GitHub Actions / lint / test 时，必须调用本 skill。

## 目标

避免代码在本地未校验就提交，导致远端 GitHub Actions 失败，浪费排查时间。

## 强制规则

1. 只要本次任务修改了代码、测试、配置、提示词或任意会影响 CI 的文件，在准备结束前都要运行本地 CI。
2. 如果用户要求“提交”“帮我 commit”“准备推送”“开 PR”，必须先运行本地 CI，成功后才能继续。
3. 如果本地 CI 失败，优先修复失败项，然后重新运行，直到通过或明确告知用户阻塞原因。
4. 不要因为“只是小改动”而跳过本地 CI。

## 标准执行命令

优先使用仓库根目录下的 PowerShell 脚本：

```powershell
pwsh -File .\scripts\lint-and-test.ps1
```

当 `black --check` 失败，需要先自动修复格式时，使用：

```powershell
pwsh -File .\scripts\lint-and-test.ps1 -FixFormatting
```

## 脚本覆盖的检查

该脚本应与 GitHub Actions 的 `lint-and-test` 对齐，至少包含：

```powershell
python -m black --check src tests
python -m ruff check src tests
python -m pytest tests -v --cov=src --cov-report=term-missing --cov-report=xml:coverage.xml
```

## 失败处理

1. `black --check` 失败：
   先运行 `pwsh -File .\scripts\lint-and-test.ps1 -FixFormatting`，再重新检查。
2. `ruff` 失败：
   修复 lint 问题后重新运行脚本。
3. `pytest` 失败：
   修复测试或实现后重新运行脚本。
4. 若环境缺少依赖：
   先执行 `python -m pip install -e .[dev]`，再重试。

## 输出要求

在向用户汇报结果时，必须说明：

- 运行了哪个本地 CI 命令
- 是否通过
- 如果失败，阻塞项是什么
- 若用户要求提交但 CI 未通过，必须明确拒绝提交并说明原因

## 示例

用户说：

- “修一下这个测试然后帮我提交”
- “这个 action 挂了，修复后提交”
- “改完直接开 PR”

你应该先修复问题，然后运行：

```powershell
pwsh -File .\scripts\lint-and-test.ps1 -FixFormatting
```

确认通过后，才进入下一步提交流程。
