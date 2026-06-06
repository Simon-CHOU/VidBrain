# Semi-Auto Pipeline Spec

## Why

当前 VidBrain 是全自动管线：视频流入 → 自动分类 → 自动 ASR → 自动 Agent 生成笔记 → 直接写入 Vault。全程无人参与，无法在关键节点进行人为质量控制。需要引入 **半自动化架构**，在保持 local-first 和 self-contained 的前提下，让用户在关键决策点介入审核。

## What Changes

- **新增 Draft Vault 机制**：Agent 生成的笔记先写入 `vault_dir/_drafts/`，经用户审核确认后才移入正式 Vault
- **新增队列审批机制**：处理前展示待处理队列，用户可选择 approve / skip / reject
- **新增分类审核机制**：unclear 分类的视频可交由用户手动判定
- **新增 `--semi` 运行模式**：一键启用所有人工审核门禁
- **新模块 `vidbrain/drafts.py`**：Draft 管理器（写草稿、列草稿、发布/删除草稿）
- **新增状态 `PENDING_REVIEW`**：标记笔记等待人工审核

## Impact

- Affected specs: 管线调度、文件输出、CLI 入口
- Affected code: `pipeline.py`, `main.py`, `config.py`, `db.py`
- New code: `vidbrain/drafts.py`

## ADDED Requirements

### Requirement: Draft Vault Workflow

系统 SHALL 在半自动模式下将 Agent 生成的笔记写入 `vault_dir/_drafts/` 子目录，而非直接写入 Vault 根目录。用户通过 CLI 命令审核草稿后，系统将确认的草稿移动到正式 Vault。

#### Scenario: 半自动模式生成草稿
- **WHEN** 用户以 `--semi` 模式运行管线
- **AND** Agent 完成一篇笔记的生成
- **THEN** 笔记写入 `vault_dir/_drafts/` 目录
- **AND** front-matter 中 `status` 为 `draft`
- **AND** DB 状态更新为 `DRAFT_PENDING`

#### Scenario: 用户审核通过草稿
- **WHEN** 用户运行 `--review-drafts` 并确认某篇草稿
- **THEN** 该笔记从 `_drafts/` 移动到 Vault 根目录
- **AND** front-matter 中 `status` 更新为 `auto-generated`（含 `reviewed: true`）
- **AND** DB 状态更新为 `SUCCESS`

#### Scenario: 用户拒绝草稿
- **WHEN** 用户运行 `--review-drafts` 并拒绝（skip/delete）某篇草稿
- **THEN** 该草稿被删除
- **AND** DB 状态更新为 `DISCARDED`

### Requirement: 队列审批机制

系统 SHALL 在半自动模式下，在开始处理视频之前展示待处理队列，允许用户逐一决定 approve / skip / reject。

#### Scenario: 半自动队列审批
- **WHEN** 用户以 `--semi` 模式运行管线
- **AND** 系统完成分类阶段
- **THEN** 系统进入交互式队列审批阶段
- **AND** 逐个展示待处理视频信息（名称、分类理由）
- **AND** 用户可选 A)pprove 处理 / S)kip 跳过 / R)eject 标记 skip / Q)uit 退出审批

#### Scenario: 全自动模式跳过审批
- **WHEN** 用户以常规模式（无 `--semi`）运行管线
- **THEN** 系统跳过队列审批，直接处理所有 tech 视频
- **AND** 行为与当前全自动模式完全一致（向后兼容）

### Requirement: 分类审核机制

系统 SHALL 允许用户对 unclear 分类的视频进行手动判定，将误分类的视频纠正为 tech 或 skip。

#### Scenario: 审核 unclear 分类
- **WHEN** 用户运行 `--review-classifications`
- **THEN** 系统逐个展示所有 `category='unclear'` 的视频名称
- **AND** 用户可选 T)ech / S)kip / P)ass 保留 unclear
- **AND** 分类结果回写 DB 的 category 字段

### Requirement: Semi Mode CLI

系统 SHALL 提供 `--semi` flag，作为启用所有人工审核门禁的快捷方式。

#### Scenario: --semi 模式全流程
- **WHEN** 用户运行 `--semi --once`
- **THEN** 执行顺序：
  1. 自动分类（不变）
  2. 分类审核（unclear 视频人工判定）
  3. 队列审批（tech 视频逐一确认）
  4. 处理已审批的视频（ASR → Agent → Draft）
  5. 草稿审核（逐篇确认发布/删除）

### Requirement: Draft Manager 模块

系统 SHALL 提供 `vidbrain/drafts.py` 模块，封装所有草稿相关操作。

#### Scenario: 列出待审核草稿
- **WHEN** 调用 `list_drafts(vault_path)` 
- **THEN** 返回 `_drafts/` 目录下所有 `.md` 文件列表

#### Scenario: 发布草稿
- **WHEN** 调用 `publish_draft(vault_path, draft_name)`
- **THEN** 草稿从 `_drafts/` 移动到 Vault 根目录，front-matter 更新

#### Scenario: 删除草稿
- **WHEN** 调用 `discard_draft(vault_path, draft_name)`
- **THEN** 草稿文件被删除

### Requirement: 新 DB 状态

系统 SHALL 支持新任务状态 `DRAFT_PENDING` 和 `DISCARDED`。

#### Scenario: 状态流转
- **WHEN** Agent 生成草稿成功 → `DRAFT_PENDING`
- **WHEN** 用户发布草稿 → `SUCCESS`
- **WHEN** 用户放弃草稿 → `DISCARDED`

### Requirement: 批量审核选项

系统 SHALL 在审核交互中支持批量操作（全部通过 / 全部跳过）。

#### Scenario: 队列审批批量操作
- **WHEN** 队列审批交互中用户选择 "All"
- **THEN** 所有剩余待审视频自动 approve
- **AND** 立即开始处理这批视频

## MODIFIED Requirements

### Requirement: 管线输出路径（修改）

旧：`process_pipeline` 直接将生成的笔记写入 `vault_dir/` 根目录。
新：当 `semi=True` 时，写入 `vault_dir/_drafts/`；`semi=False` 时，写入 `vault_dir/` 根目录（保持向后兼容）。

### Requirement: CLI 入口（修改）

新增以下 CLI 参数：
- `--semi`：启用半自动模式（包含分类审核 + 队列审批 + 草稿审核）
- `--review-drafts`：独立进入草稿审核模式
- `--review-classifications`：独立进入分类审核模式（已部分存在于 `--classify-only` 但无交互）
