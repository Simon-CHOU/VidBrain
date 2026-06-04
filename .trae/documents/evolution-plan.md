# VidBrain 演进方案：分级分批自迭代知识库

## 决策记录

| 决策项 | 选择 |
|--------|------|
| 分类兜底 | 标记 UNCLEAR，暂不处理 |
| 批次大小 | 5 个/批 |
| 运行模式 | 持续模式（每 2h 一批）+ 单次模式均可；每次启动先分类+处理一批，再进入监听 |

---

## 一、需求拆解

| 需求 | 说明 |
|------|------|
| 不卡死桌面 | 小批量处理，控制资源占用 |
| 分级分批 | 先分类，仅处理有价值的视频 |
| 跑完全量现存视频 | 长期任务，分多次完成 |
| 定时运行 | 周期性处理新视频 |
| 知识库自迭代 | 完善旧笔记的双链，生成总结/MOC |

---

## 二、架构变更

### 2.1 新增模块

```
vidbrain/
├── classifier.py    # 视频文件名分类器（关键词匹配）
├── refiner.py       # 知识库精炼器（完善双链、生成 MOC）
└── ... 现有模块
```

注：不单独创建 scheduler.py — 调度逻辑直接集成到 main.py 的 interval 模式中。

### 2.2 数据库扩展

```sql
ALTER TABLE video_pipeline ADD COLUMN category TEXT;           -- tech/unclear/skip
ALTER TABLE video_pipeline ADD COLUMN classify_reason TEXT;    -- 分类理由
```

### 2.3 核心流程

```
启动
 │
 ├─ 扫描 I:\web-videos → 列出所有未分类的 .mp4
 │
 ├─ 分类阶段（仅读文件名，不做 ASR）
 │   ├─ 技术/知识/课程/思辨/面试 → category='tech'
 │   ├─ 抖音/娱乐/无关内容 → category='skip'
 │   └─ 模糊不清 → category='unclear'
 │
 ├─ 处理阶段（每次最多 5 个 tech 视频）
 │   ├─ ASR → Agent → 写入 Vault
 │   └─ 处理完毕退出（--once）或等待下一周期（--interval）
 │
 └─ 监听阶段（持续模式）
     └─ watchdog 监听新文件，分类后排队处理
```

---

## 三、分类策略

### 关键词白名单（命中 → tech）

```
技术类:  编程, Python, Go, Rust, 算法, 架构, 源码, 区块链,
         Web3, 智能合约, Solidity, AI, LLM, 机器学习, Kubernetes,
         Docker, Linux, 数据库, 后端, 前端, DevOps, 面试, 面经

知识类:  科普, 原理, 深度, 解析, 拆解, 底层, 设计, 模式,
         方法论, 思维, 认知, 经济学, 物理, 数学

课程类:  教程, 课程, 教学, 入门, 进阶, 实战, 训练营, 公开课

观点类:  观点, 思辨, 评论, 分析, 讨论, 对话, 对谈, 播客,
         访谈, 圆桌, 辩论, 座谈

B站标识: BV, BV1  → 优先处理
技术博主: @ 后跟英文名 → 优先处理
```

### 关键词黑名单（命中 → skip）

```
娱乐类:  抖音, 快手, 搞笑, 娱乐, 日常, vlog, 吃播, 带货
```

### 兜底

- 未命中任何规则 → category='unclear'，暂不处理

---

## 四、CLI 参数设计

```bash
# 单次模式：分类 + 处理 5 个，退出
python -m vidbrain.main --batch-size 5 --once

# 持续模式：先处理 5 个，然后每 2 小时再处理一批
python -m vidbrain.main --batch-size 5 --interval 2h

# 仅分类，不处理（预览）
python -m vidbrain.main --classify-only

# 精炼知识库
python -m vidbrain.main --refine
```

---

## 五、实施步骤

| 步骤 | 内容 |
|------|------|
| 1 | 扩展 DB（category + classify_reason 字段） |
| 2 | 创建 classifier.py（关键词分类器） |
| 3 | config.py + main.py 新增 batch-size / interval / classify-only 参数 |
| 4 | pipeline.py 集成分类步骤 + 跳过非 tech 视频 |
| 5 | main.py 实现 interval 定时调度 + 断点续跑 |
| 6 | 创建 refiner.py（孤立笔记检测 + MOC 生成） |
