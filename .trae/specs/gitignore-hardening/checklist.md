# Checklist

- [x] `.gitignore` 包含 `vidbrain_vault/` 规则
- [x] `.gitignore` 包含 `_drafts/` 规则（匹配任意路径）
- [x] `.gitignore` 包含 `*.db-wal` 规则
- [x] `.gitignore` 包含 `*.db-shm` 规则
- [x] `.gitignore` 包含 `*.db-journal` 规则
- [x] `git status` 确认 vidbrain_vault/ 不在 untracked 中
- [x] `git status` 确认无任何 `*.db-*` SQLite 临时文件被追踪
