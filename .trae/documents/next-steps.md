# VidBrain 下一步计划

## 当前状态总览

### 已完成的模块

| 模块 | 状态 | 最后验证 |
|------|------|---------|
| ASR 引擎 (asr_engine.py) | ✅ 完成 | 用 tiny 模型成功转录 2 个视频 |
| Agent 工作流 (agent_graph.py) | ✅ 完成 | DeepSeek API 清洗 + 双链织网正常 |
| 管线调度 (pipeline.py) | ✅ 完成 | 4 阶段管线跑通，无 archive 残留 |
| 文件监听 (watcher.py) | ✅ 完成 | 已集成分类器，仅处理 tech 视频 |
| 分类器 (classifier.py) | ✅ 完成 | 10121 视频 1 秒分类完成: tech=8749 |
| 数据库 (db.py) | ✅ 完成 | 含分类字段、批量操作方法 |
| 日志 (logger.py) | ✅ 完成 | 含敏感信息脱敏 |
| 知识库精炼 (refiner.py) | ✅ 完成 | 孤立笔记双链补充 + MOC 生成测试通过 |
| 配置管理 (config.py) | ✅ 完成 | 自包含设计，模型缓存重定向到 `.model_cache/` |
| 主入口 (main.py) | ✅ 完成 | 所有 CLI 参数（batch-size/interval/classify-only/refine）就绪 |

### 已知问题

| # | 问题 | 位置 | 严重度 |
|---|------|------|--------|
| 1 | 3 处冗余 import | `main.py:134,141,217` | 低（不影响运行） |
| 2 | spec.md 仍提及 archive_dir 归档流程 | `spec.md:33,41` | 低（设计文档未同步） |

### 未被完整验证的关键路径

| 路径 | 说明 |
|------|------|
| **批量分类 + 处理** | `--batch-size 5 --once` 从分类到 ASR 再到 Agent 的完整链路 |
| **interval 持续模式** | `--interval 2h` 定时触发批处理 |
| **watchdog 持续监听** | 新文件自动分类后异步处理 |
| **large-v3 模型** | 仅测试过 tiny，未测试 large-v3 |

---

## 建议方案

### 建议：先冒烟，再迭代

```
Step 1 ─ 清理 3 处冗余 import + 更新 spec.md（5 分钟）
Step 2 ─ 冒烟测试：--batch-size 3 --model-size tiny --once（约 15 分钟）
Step 3 ─ 根据冒烟结果决定迭代方向
```

**冒烟测试验证清单**：
- [ ] 分类阶段正确运行（已有 8749 tech 待处理）
- [ ] 仅处理 tech 类视频（跳过 skip/unclear）
- [ ] ASR 转录成功
- [ ] Agent 清洗 + 双链织网成功
- [ ] 笔记写入 vault 目录
- [ ] pipeline.db 状态正确流转
- [ ] 不移除/不修改 I:\web-videos 下的文件

### 后续迭代方向（Option A/B/C）

根据冒烟结果，可选迭代方向：

| 方向 | 说明 | 预估工作量 |
|------|------|-----------|
| **A. 补全分类规则** | 检查 unclear 的 946 个视频，补充关键词规则 | 小 |
| **B. 优化 ASR 效率** | 支持多线程并行处理、断点续传、进度保存 | 中 |
| **C. 增强 refine** | 支持定期自动触发、跨笔记内容关联分析 | 中 |

---

## 决策选项

请选择下一步方向：
