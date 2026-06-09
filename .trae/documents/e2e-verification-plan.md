# VidBrain E2E 功能验收计划

## 背景

用户刚完成项目重构（添加 ruff lint CI 门限），GitHub CI 已全部通过（Python 3.10/3.11/3.12）。
需要在本地实际运行项目，端到端验收功能是否正常。

## 环境现状

| 项目 | 状态 |
|------|------|
| Python | 3.11.9 可用 |
| uv | 0.9.28 可用 |
| DEEPSEEK_API_KEY | 已配置（系统环境变量） |
| ffmpeg | 已安装 |
| 输入目录 `I:\web-videos` | 存在真实 .mp4 文件（至少 5 个） |
| 工作目录 | `f:\ML\bailian-playground\VidBrain` |

## Phase 1: 环境准备与依赖安装

**目标**: 确保所有依赖安装正确。

1. 运行 `uv sync` 安装/更新所有依赖
2. 运行 `pip show vidbrain` 或 `python -c "import vidbrain"` 验证包安装

## Phase 2: 代码质量检查（复现 CI Lint 步骤）

**目标**: 在本地验证 ruff/black/mypy/pylint 全部通过。

1. `ruff check src/ tests/` — Lint 检查
2. `black --check src/ tests/` — 代码格式检查
3. `mypy src/ --ignore-missing-imports` — 类型检查
4. `pylint src/ --fail-under=7.0 --max-line-length=100` — 代码质量

## Phase 3: 单元测试（复现 CI Test 步骤）

**目标**: 确保所有单元测试通过。

1. `pytest tests/ -v --cov=src --cov-report=term-missing` — 运行测试并输出覆盖率
2. 确认所有测试通过，覆盖率在合理范围

## Phase 4: 轻量级 E2E（--classify-only 模式）

**目标**: 在不调用 ASR/LLM 的情况下，验证分类器 + 数据库核心链路正常。

1. 创建一个临时测试目录 `./test_e2e_light/` 作为 vault_dir
2. 在该目录下创建几个 .mp4 文件（使用 ffmpeg 生成 1 秒静音视频）：
   - `Python教程入门.mp4` → 预期分类为 `tech`
   - `舞蹈教学基本功.mp4` → 预期分类为 `skip`
   - `随便看看.mp4` → 预期分类为 `unclear`
3. 运行: `python -m vidbrain.main --vault-dir ./test_e2e_light/vault --input-dir ./test_e2e_light/input --classify-only --once --db-path ./test_e2e_light/pipeline.db`
4. 验证:
   - 分类结果正确（tech/skip/unclear）
   - SQLite 数据库 `pipeline.db` 中有对应记录
   - 日志输出包含分类汇总

## Phase 5: 全流程 E2E（处理真实视频）

**目标**: 对 I:\web-videos 中的一个真实小视频执行完整管线（ASR → Agent → 写入 Vault）。

1. 创建一个临时 vault 目录用于本次测试
2. 从 I:\web-videos 选取 1 个最小的 .mp4 文件（约 680KB 的 `_000002gc76skmacfj2bokfhpj7of9pb-1-152111110012.mp4`）
3. 将该文件复制到临时测试输入目录，重命名为 `Python技术分享.mp4`（确保分类为 tech）
4. 运行完整管线（需 DEEPSEEK_API_KEY 环境变量）：
   ```
   python -m vidbrain.main --vault-dir ./test_e2e_full/vault --input-dir ./test_e2e_full/input --once --model-size tiny --batch-size 1 --db-path ./test_e2e_full/pipeline.db
   ```
5. 验证:
   - ASR 转录完成（数据库中有 transcription 记录）
   - Agent 处理完成（数据库状态为 COMPLETED）
   - Vault 目录中生成 .md 笔记文件（含 YAML front-matter）
   - 笔记内容包含 Obsidian [[双链]]
   - 无严重错误日志

## Phase 6: 清理

- 删除临时测试目录 `./test_e2e_light/` 和 `./test_e2e_full/`

## 注意事项

- Phase 5 会调用 DeepSeek API，消耗少量 tokens（预估 < 10K tokens）
- ASR 模型（tiny）首次运行会自动下载，可能需要几分钟
- 若 Phase 5 中某个环节失败，需根据错误信息判断是环境问题还是代码 Bug
- Phase 4/5 使用的临时目录均在项目根目录下创建，不影响已有数据
- `I:\web-videos` 是只读目录，程序不会修改其中的文件
