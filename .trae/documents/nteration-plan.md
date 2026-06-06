# Phase A: 分类规则补全

## 当前问题

| 问题 | 影响 | 严重度 |
|------|------|--------|
| BV1 规则误分类 | 非技术 B站视频（cosplay/日常）被标记为 tech，浪费 ASR+API | 中 |
| 946 个 unclear 待处理 | 可能有技术视频被遗漏 | 低 |

## 实施步骤

### Step 1：审查 unclear 文件规律

先看 946 个 unclear 文件的命名模式，决定是否需要补充关键词。

```bash
# 从 pipeline.db 查询 unclear 分类的文件名
uv run python -c "
import sqlite3
conn = sqlite3.connect('pipeline.db')
rows = conn.execute(\"SELECT video_name FROM video_pipeline WHERE category='unclear' LIMIT 50\").fetchall()
for r in rows: print(r[0])
"
```

### Step 2：修复分类器

**修改 `classifier.py`**：
- 降低 BV1 规则的权重：`BV` 或 `BV1` 必须同时满足文件名含至少一个白名单关键词，才标记为 tech
- 如果仅命中 BV 规则，标记为 `unclear` 而非 `tech`
- 补充从 unclear 中发现的常见技术关键词

### Step 3：重新分类 + 冒烟验证

```bash
# 重新分类（已有 DB 只会补充分类，不会重复处理）
uv run python -m vidbrain.main --vault-dir .\smoke_test_vault --classify-only

# 冒烟验证新规则
uv run python -m vidbrain.main --model-size tiny --batch-size 3 --vault-dir .\smoke_test_vault --once
```

---

# Phase B: 测试体系建立

测试金字塔：

```
    ╱╲              E2E: smoke_test_vault/ + pipeline.db
   ╱  ╲             集成: classifier + db + asr_engine
  ╱────╲            单元: parse_links, classify_video, parse_interval
 ╱      ╲
╱────────╲
```

### 文件结构

```
vidbrain/
├── tests/
│   ├── __init__.py
│   ├── test_classifier.py    # 单元：classify_video 各种场景
│   ├── test_db.py            # 单元：DB 操作
│   ├── test_refiner.py       # 单元：parse_links, analyze_links
│   ├── test_main.py          # 单元：parse_interval
│   └── test_integration.py   # 集成：分类→ASR→Agent→Vault（mock API）
├── ... 现有模块
```

### 运行方式

```bash
uv run pytest vidbrain/tests/ -v
```
