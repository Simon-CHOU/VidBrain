# Tasks

## Task 1: 扩充 .gitignore 排除规则
- [x] 在 `.gitignore` 中新增以下排除规则：
  - `vidbrain_vault/` — Obsidian 知识库输出目录（运行时产物）
  - `_drafts/` — 半自动模式草稿目录（任意路径下）
  - `*.db-wal`、`*.db-shm`、`*.db-journal` — SQLite WAL 模式临时文件

## Task 2: 验证 git status 无运行时产物
- [x] 运行 `git status` 确认 `vidbrain_vault/`、`_drafts/`、`*.db-*` 均不在 untracked 列表中
- [x] 确认 `logs/`、`reports/`、`.model_cache/`、`*.db` 已正确排除

# Task Dependencies
- Task 2 depends on Task 1
