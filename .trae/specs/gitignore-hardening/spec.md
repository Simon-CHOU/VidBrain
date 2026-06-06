# Gitignore 加固 Spec — 确保代码仓库保持无状态纯函数特性

## Why
当前 `.gitignore` 缺少对知识库输出目录 (`vidbrain_vault/`) 和 SQLite WAL 文件的排除，导致持续运行后这些运行时产物会污染代码仓库。repo 应当像纯函数一样：接收输入（源代码），输出到外部（logs/、reports/、vault/），自身永不膨胀。

## What Changes
- **BREAKING**: 无，仅新增 ignore 规则
- `vidbrain_vault/` 及其子目录 `_drafts/` 排除出 git
- SQLite WAL/SHM/Journal 临时文件排除
- 确保所有「输入输出的产物和中间产物」都不会被 git 追踪

## Impact
- Affected specs: 无（纯配置变更）
- Affected code: `.gitignore` 单文件

## ADDED Requirements

### Requirement: 运行时产物完全排除
系统 SHALL 在 `.gitignore` 中排除所有程序运行时生成的「输出产物」和「中间产物」，确保代码仓库不会因持续运行而膨胀。

#### Scenario: 知识库输出目录被排除
- **WHEN** VidBrain 运行并产出 `.md` 笔记到 `vidbrain_vault/`
- **THEN** `vidbrain_vault/` 及其所有内容不会被 git 追踪

#### Scenario: 草稿目录被排除
- **WHEN** 半自动模式生成草稿到 `_drafts/` 子目录
- **THEN** 任意路径下的 `_drafts/` 目录均不会被 git 追踪

#### Scenario: SQLite 临时文件被排除
- **WHEN** SQLite 数据库写入产生 `*.db-wal`、`*.db-shm`、`*.db-journal` 文件
- **THEN** 这些文件不会被 git 追踪

### Requirement: 仓库保持无状态
系统 SHALL 确保所有被 `.gitignore` 排除的目录和文件在 clone 后不存在，程序启动时通过自身逻辑创建。

#### Scenario: 新 clone 后目录结构干净
- **WHEN** 开发者 `git clone` 本仓库
- **THEN** repo 根目录下不应包含 `vidbrain_vault/`、`logs/`、`reports/`、`*.db`，这些目录由程序运行时自动创建
