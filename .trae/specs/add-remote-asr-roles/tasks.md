# Tasks
- [x] Task 1: 扩展 CLI 与配置模型，定义 `primary/worker` 角色与远端 endpoint 参数。
  - [x] SubTask 1.1: 在 `src/cli.py` 新增角色、远端主机、端口、超时、健康检查、熔断冷却相关参数
  - [x] SubTask 1.2: 在 `src/models/config.py` 扩展 `PipelineConfig`，承载远端 worker 路由配置
  - [x] SubTask 1.3: 补充 CLI/配置层测试，验证默认值与参数组合

- [x] Task 2: 拆分启动入口，落地 `primary` 与 `worker` 两条运行路径。
  - [x] SubTask 2.1: 在 `src/main.py` 中将现有完整主流程收敛为 `primary` 路径
  - [x] SubTask 2.2: 增加 `worker` 路径，只启动远端 ASR 服务与健康接口
  - [x] SubTask 2.3: 确保 `worker` 模式不初始化数据库、watcher、vault、LLM 主流程

- [x] Task 3: 新增远端 ASR 客户端与路由策略，实现远端优先、本地 CPU 回退。
  - [x] SubTask 3.1: 在 `src/services/` 增加远端 ASR 客户端抽象，封装请求与响应映射
  - [x] SubTask 3.2: 将远端路由接入主控侧 ASR 选择逻辑，远端不可用时回退本地 CPU
  - [x] SubTask 3.3: 保持当前 `cpu/vulkan` 单机行为向后兼容

- [x] Task 4: 实现健康检查、熔断、冷却与自动恢复探测。
  - [x] SubTask 4.1: 定义远端节点状态机与失败阈值、恢复阈值
  - [x] SubTask 4.2: 在主控任务处理前后接入健康检查与熔断判定
  - [x] SubTask 4.3: 在冷却窗口后执行后台恢复探测，成功后恢复远端优先

- [x] Task 5: 补充测试与验证，覆盖首版远端 ASR 行为。
  - [x] SubTask 5.1: 增加主控配置远端 endpoint 的单元测试
  - [x] SubTask 5.2: 增加远端成功、超时失败、本地回退的测试
  - [x] SubTask 5.3: 增加熔断、冷却、恢复探测的测试
  - [x] SubTask 5.4: 运行本地 lint-and-test，确保新增功能满足提交前 CI 约束

# Task Dependencies
- [Task 2] depends on [Task 1]
- [Task 3] depends on [Task 1] and [Task 2]
- [Task 4] depends on [Task 3]
- [Task 5] depends on [Task 1], [Task 3], and [Task 4]
