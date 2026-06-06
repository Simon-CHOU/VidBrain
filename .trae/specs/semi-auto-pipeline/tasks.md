# Tasks

- [x] Task 1: 创建 Draft Manager 模块 (`vidbrain/drafts.py`)
  - [x] 1.1 实现 `write_draft(vault_path, file_name, content)` — 将笔记内容写入 `_drafts/` 目录，附带 draft front-matter
  - [x] 1.2 实现 `list_drafts(vault_path)` — 列出 `_drafts/` 下所有 `.md` 文件，返回文件名列表
  - [x] 1.3 实现 `publish_draft(vault_path, draft_name)` — 将草稿从 `_drafts/` 移动到 Vault 根目录，更新 front-matter：`status: auto-generated`，添加 `reviewed: true, reviewed_at: <timestamp>`
  - [x] 1.4 实现 `discard_draft(vault_path, draft_name)` — 删除草稿文件
  - [x] 1.5 编写 `tests/test_drafts.py` 单元测试（覆盖 write/list/publish/discard 四个操作）

- [x] Task 2: 新增 DB 状态和 `semi` 配置字段
  - [x] 2.1 在 `PipelineConfig` 中新增 `semi: bool = False`、`review_drafts: bool = False`、`review_classifications: bool = False` 字段
  - [x] 2.2 DB 新增 `update_status_by_name()` 方法支持草稿发布/删除时的状态回调

- [x] Task 3: 改造管线输出 (`pipeline.py`)
  - [x] 3.1 `process_pipeline` 接收 `semi` 参数，当 `semi=True` 时调用 `write_draft` 而非直接写入 Vault 根目录
  - [x] 3.2 写入草稿成功后 DB 状态更新为 `DRAFT_PENDING` 而非 `SUCCESS`

- [x] Task 4: 实现交互式审核 CLI (`main.py`)
  - [x] 4.1 实现 `review_classifications(db)` — 交互式审核 unclear 视频，输入 T/S/P，回写 DB
  - [x] 4.2 实现 `review_queue(db)` — 交互式审批处理队列，输入 A/S/R/Q，返回 approved_id_list
  - [x] 4.3 实现 `review_drafts_vault(cfg, db)` — 交互式审核草稿，逐篇展示标题，输入 P/D/S/`*`，调用 publish_draft / discard_draft，更新 DB 状态
  - [x] 4.4 新增 CLI 参数解析：`--semi` (action='store_true')、`--review-drafts` (action='store_true')、`--review-classifications` (action='store_true')
  - [x] 4.5 `build_config` 映射新参数到 PipelineConfig
  - [x] 4.6 在 `main()` 中编排 semi 模式全流程：分类 → 分类审核 → 队列审批 → 仅处理已审批视频 → 草稿审核

- [x] Task 5: 编写集成测试
  - [x] 5.1 `test_drafts.py` — drafts.py 四个函数的单元测试（9 个用例）
  - [x] 5.2 验证现有测试全部通过（40/40，向后兼容）

# Task Dependencies

- Task 2 依赖 Task 1（drafts.py 的接口决定 config 字段设计）
- Task 3 依赖 Task 1、Task 2
- Task 4 依赖 Task 1、Task 2、Task 3
- Task 5 可与 Task 3、Task 4 并行
