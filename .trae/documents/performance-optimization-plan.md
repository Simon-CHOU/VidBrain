# VidBrain 性能优化方案

## 当前状态分析

### 硬件环境
- **CPU**: Ryzen 4650G (6核12线程, Zen2架构, 带 Vega 8 iGPU)
- **RAM**: CPU间歇性打满, RAM使用率约50% (有大量空闲)
- **GPU**: Vega 8 iGPU 几乎空载

### 瓶颈定位 (按影响从大到小)

| 瓶颈 | 位置 | 影响 | 原因 |
|------|------|------|------|
| **ASR 推理** | `asr_engine.py:225-244` | 最大 | `faster-whisper` 硬编码 `device="cpu"`, `compute_type="int8"`, 视频串行处理 |
| **管线串行** | `pipeline.py:85-265` / `main.py:260-299` | 大 | ASR → Agent → 写入全过程串行, 多视频逐个处理, `ThreadPoolExecutor(max_workers=1)` |
| **Vault 重复扫描** | `pipeline.py:127-133` | 中 | 每个视频都做 `vault_path.rglob("*.md")` 全量扫描 |
| **Embedding 暴力搜索** | `embedding.py:165-178` / `181-248` | 小(可选模块) | O(N) 余弦相似度搜索, 纯 Python k-means |
| **Agent 网络IO** | `pipeline.py:162-173` | 不可控 | DeepSeek API 延迟, 已为串行三步调用 |

### 当前资源利用
- **CPU**: ASR 阶段通过 CTranslate2 多线程几乎打满 (默认 `cpu_count()-1` = 11线程)
- **RAM**: Whisper模型常驻 ~500MB(small)/~3GB(large-v3) + Embedding向量, 仍有~50%空闲
- **GPU**: 完全未使用

---

## 关键约束: Vega 8 GPU 加速的现实限制

### faster-whisper / CTranslate2 的 GPU 后端
- CTranslate2 官方只支持 **NVIDIA CUDA** 和 **AMD ROCm (离散显卡)**
- **Vega 8 是 APU 集显**, ROCm 不官方支持 APU
- 因此 **faster-whisper 无法直接使用 Vega 8 加速**

### 可行的 GPU 替代方案

| 方案 | 可行性 | 性能收益 | 实现复杂度 |
|------|--------|----------|------------|
| **whisper.cpp + Vulkan** | 高 | 2-5x 加速 | 中 (需要编译 whisper.cpp, 集成 Python 绑定) |
| **ONNX Runtime + DirectML** | 中 | 2-4x 加速 | 中高 (需要导出 Whisper ONNX 模型) |
| **ffmpeg + Vulkan 音频解码** | 高 | 很小 | 低 (只需添加预处理步骤) |
| **OpenVINO + whisper** | 低 (Intel 优先) | 不确定 | 高 |

> **推荐路径**: whisper.cpp Vulkan backend 是对 Vega 8 最成熟、收益最大的 GPU 加速方案。

---

## 优化方案 (分两阶段)

### 阶段一: RAM 空间换时间 (低风险, 立即见效)

#### 1.1 管线并行化 - 视频间并行处理

**文件**: `vidbrain/main.py` — `process_batch()` 函数 (L260-299)

**现状**: for 循环逐个串行处理视频, `ThreadPoolExecutor` 只用于 watchdog 回调 (max_workers=1)

**改动**:
- 将 `ThreadPoolExecutor` 的 `max_workers` 改为 `min(3, cpu_count()//2)` (约3个并发)
- 用 `executor.submit()` 并行提交多个 `process_pipeline()` 任务
- 利用空闲 RAM 容纳多个并行 Whisper 推理实例 (每个视频约增加 数百MB 临时内存)

**风险**: Whisper 模型本身是全局单例, CTranslate2 在多线程下安全, 但需要确保 DB 线程安全已有锁 (`db.py` 已使用 `threading.Lock`)

**收益**: 理论吞吐量提升 2-3x

#### 1.2 Vault 笔记列表缓存

**文件**: `vidbrain/pipeline.py` — `process_pipeline()` L125-133

**现状**: 每个视频处理时都做 `vault_path.rglob("*.md")` 全量扫描

**改动**:
- 新增 `VaultCache` 类, 缓存 `{stem: quality_score}` 字典
- 启动时扫描一次, 后续增量更新 (watchdog 检测新 .md 文件时追加)
- 有笔记变更时整体重扫 (低频操作)

**收益**: 减少大量磁盘 I/O, 对于有 100+ 笔记的 Vault 每次节省 ~50ms-200ms

#### 1.3 Agent 预取 Context (RAM 缓冲)

**文件**: `vidbrain/pipeline.py` — Step 2 (L120-133)

**现状**: Step 2 中只收集 `existing_notes` 的 stem 列表
**实体笔记内容**在 `agent_graph.py` 的 link 阶段才读取 (每次 LLM 调用时临时读磁盘)

**改动**:
- 预先将 Agent 可能需要引用的 top-N 篇高质量笔记内容读入内存
- 传给 `AgentState` 的 `existing_notes` 从 `list[str]` 扩展为 `list[dict]`, 包含 stem + content 摘要
- 或直接在 `agent_graph.py` 中的 link 阶段使用内存缓存避免重复磁盘读取

**收益**: Agent 阶段减少磁盘 I/O, 提升 Agent 响应速度

#### 1.4 Embedding 向量化和检索的 numpy 加速

**文件**: `vidbrain/embedding.py` — `EmbeddingEngine.similarity()` (L89-96), `EmbeddingStore.find_similar()` (L165-178), `_kmeans()` (L181-248)

**现状**: 纯 Python 实现, 逐元素循环

**改动**:
- 添加 `numpy` 依赖 (或 `numpy` + `scipy` 用于 k-means)
- 用 numpy 批量矩阵运算替代逐元素循环
- k-means 可用 scipy 的 `kmeans2` 或 sklearn 的 `MiniBatchKMeans`
- 相似度检索用 `np.dot(vectors, query) / (norms * query_norm)` 批量计算

**收益**: 对于 500+ 向量的检索和聚类, 速度提升 10-50x

---

### 阶段二: GPU 硬件加速

#### 2.1 [核心] ASR 引擎切换为 whisper.cpp + Vulkan

**新文件**: `vidbrain/asr_engine_vulkan.py`
**修改**: `vidbrain/asr_engine.py` (保持兼容), `vidbrain/config.py`, `vidbrain/main.py`

**方案**:
1. 编译 whisper.cpp Windows 版本 (启用 Vulkan: `cmake -DGGML_VULKAN=ON`)
2. 通过 `subprocess` 调用 `whisper-cli.exe` 或使用 Python 绑定 `pywhispercpp`
3. 新增 CLI 参数 `--asr-backend {cpu|vulkan}` (默认 cpu, 向后兼容)
4. `prepare_model()` 增加 GPU 模型加载路径
5. `transcribe()` 适配 whisper.cpp 输出格式为现有 `list[dict[str, Any]]`

**whisper.cpp 调用方式** (subprocess 方案, 最简单):
```powershell
whisper-cli.exe -m ggml-model-small.bin -f audio.wav -l zh --output-json
```

**降级策略**: 如果 Vulkan 不可用或模型加载失败, 自动回退到 faster-whisper CPU 路径

**模型准备**:
- 使用 `scripts/download-ggml-model.cmd small` (whisper.cpp 自带脚本) 下载 GGML 格式模型
- 模型大小与 faster-whisper 相同 (small ≈ 500MB)

**收益**: ASR 推理速度提升 2-5x, CPU 负载大幅下降

#### 2.2 ffmpeg Vulkan 音频预提取

**文件**: `vidbrain/pipeline.py` — 新增 Step 0

**现状**: faster-whisper 内部解码音频, 无 ffmpeg 调用

**改动**:
- 在 ASR 转录前, 用 ffmpeg Vulkan 加速解码提取音频为 WAV
- 命令: `ffmpeg -vulkan_device 0 -i input.mp4 -acodec pcm_s16le -ar 16000 audio.wav`
- 然后传给 ASR 引擎处理 WAV 文件 (避免重复解码)
- WAV 文件存入临时目录, 处理完后清理

**收益**: 视频→音频解码从 CPU 移到 GPU (收益较小, 但能减轻 CPU 负载)

**注意**: 经调研, ffmpeg 的 Vulkan 主要用于视频编解码 (h264/hevc), 对音频解封装的加速有限。此项收益可能不明显, 作为可选优化。

#### 2.3 (备选) ONNX Runtime + DirectML 方案

如果 whisper.cpp Vulkan 有兼容性问题, 备选方案:
- 使用 `whisper-onnx` 导出 Whisper 模型为 ONNX 格式
- 通过 ONNX Runtime 的 DirectML 执行提供程序在 Vega 8 上推理
- 优点: Windows 原生支持, 无需额外编译
- 缺点: 性能可能不如 whisper.cpp Vulkan

---

## 实施优先级与建议顺序

| 优先级 | 优化项 | 阶段 | 预估收益 | 风险 |
|--------|--------|------|----------|------|
| **P0** | 1.1 管线并行化 | 阶段一 | 吞吐量 2-3x | 低 |
| **P0** | 1.2 Vault 缓存 | 阶段一 | 减少 I/O | 低 |
| **P1** | 2.1 ASR Vulkan 加速 | 阶段二 | 推理 2-5x | 中 (需编译依赖) |
| **P1** | 1.4 Embedding numpy | 阶段一 | 检索 10-50x | 低 |
| **P2** | 1.3 Agent 预取 | 阶段一 | 减少 I/O | 低 |
| **P2** | 2.2 ffmpeg Vulkan | 阶段二 | 少量 CPU 缓解 | 低 |
| **P3** | 2.3 ONNX DirectML | 阶段二 | 备选方案 | 中高 |

---

## 验证方法

1. **管线并行化**: 处理同一批3个视频, 对比优化前后的总耗时, 确认吞吐量提升
2. **Vault缓存**: 检查日志中 vault 扫描次数, 确认缓存命中
3. **ASR Vulkan**: 对比同一视频的 CPU vs Vulkan 转录耗时, 用 `--asr-backend` 切换
4. **整体验证**: 运行 `python -m vidbrain.main --once --batch-size 5`, 对比优化前后
   - CPU 占用率
   - GPU 占用率 (任务管理器)
   - 单视频平均处理时间
   - 总吞吐量 (视频/小时)

---

## 风险评估

- **ASR Vulkan 最大风险**: whisper.cpp 需要本地编译, 编译环境和 Vulkan SDK 依赖可能遇到兼容性问题
- **并行化风险**: CTranslate2 模型单例在多线程下需要验证稳定性; DB 已有锁但需测试并发写入
- **降级保障**: 所有 GPU 特性都有 CPU fallback, 不会破坏现有功能
