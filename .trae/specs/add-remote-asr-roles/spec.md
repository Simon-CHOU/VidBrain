# 远端 ASR 主控/节点角色化 Spec

## Why
当前 VidBrain 只有单机 `cpu/vulkan` ASR 路径，无法把 Laptop 作为长期稳定的远端算力节点接入。需要在保持单主控架构的前提下，引入 `primary/worker` 角色化启动与显式远端 endpoint 接入能力，让主机稳定使用远端 GPU，并在远端不可用时自动回退本机 CPU。

## What Changes
- 新增 `primary/worker` 角色化运行模式，保持 `primary` 为默认角色
- 新增远端 ASR endpoint 配置，允许主控显式指定 worker 主机名/IP 与端口
- 新增远端 ASR 健康检查、失败回退、熔断冷却、自动恢复探测
- 新增远端 ASR 客户端抽象，主控优先远端，失败后回退本地 CPU
- 新增 worker 模式最小运行面，只启动 ASR 服务，不启动数据库、watcher、vault、LLM 主流程
- 明确首版不包含自动局域网扫描、mDNS/zeroconf 自动发现

## Impact
- Affected specs: CLI 入口、运行模式、ASR 路由、任务失败回退
- Affected code: `src/cli.py`, `src/models/config.py`, `src/main.py`, `src/services/asr_service.py`, `src/services/pipeline_service.py`, `tests/`

## ADDED Requirements
### Requirement: 角色化启动模式
系统 SHALL 支持 `primary` 与 `worker` 两种运行角色，并默认以 `primary` 角色启动。

#### Scenario: 默认主控启动
- **WHEN** 用户以现有方式启动程序且未显式指定角色
- **THEN** 系统以 `primary` 角色运行
- **AND** 保持当前数据库、分类、watcher、pipeline 主流程行为

#### Scenario: 显式 worker 启动
- **WHEN** 用户使用 `worker` 角色启动程序
- **THEN** 系统只启动远端 ASR 服务能力
- **AND** 不启动数据库初始化、watcher、vault 写入与 LLM 主流程

### Requirement: 显式远端节点配置
系统 SHALL 允许 `primary` 角色通过显式主机名/IP 与端口连接远端 worker。

#### Scenario: 主控配置远端 endpoint
- **WHEN** 用户为 `primary` 角色提供远端主机名/IP 与端口
- **THEN** 系统使用该 endpoint 作为唯一主发现路径
- **AND** 不依赖自动扫描局域网或 mDNS

#### Scenario: 未配置远端 endpoint
- **WHEN** 用户未提供远端主机名/IP
- **THEN** 系统继续使用本地 CPU ASR
- **AND** 行为与当前单机模式兼容

### Requirement: 远端优先与本地回退
系统 SHALL 在远端 worker 可用时优先使用远端 ASR，并在失败时以任务级回退到本地 CPU ASR。

#### Scenario: 远端可用
- **WHEN** `primary` 成功探测远端 worker ready
- **THEN** 新任务优先发送到远端 worker 执行 ASR

#### Scenario: 远端请求失败
- **WHEN** 远端 ASR 请求超时、连接失败或返回不可用状态
- **THEN** 当前任务回退到本地 CPU ASR 完成
- **AND** 不中断整条处理管线

### Requirement: 健康检查与熔断恢复
系统 SHALL 通过健康检查、熔断、冷却窗口与后台恢复探测控制远端切换，而不是通过端口扫描直接切换。

#### Scenario: 连续失败进入熔断
- **WHEN** 远端 worker 连续达到失败阈值
- **THEN** 系统进入远端熔断状态
- **AND** 后续任务在冷却窗口内直接使用本地 CPU ASR

#### Scenario: 冷却后自动恢复
- **WHEN** 冷却窗口结束且后台健康检查连续成功达到恢复阈值
- **THEN** 系统重新将后续任务切回远端 worker

### Requirement: Worker 最小运行面
系统 SHALL 将 worker 模式限制为远端 ASR 节点职责，不承担主控职责。

#### Scenario: worker 模式职责边界
- **WHEN** 系统以 worker 角色运行
- **THEN** 仅暴露远端 ASR 服务与健康接口
- **AND** 不创建 pipeline 数据库
- **AND** 不监听视频目录
- **AND** 不执行 LLM / vault 写入

## MODIFIED Requirements
### Requirement: ASR 后端选择
系统 SHALL 将原本的单机 `cpu/vulkan` 后端选择扩展为“本地后端 + 可选远端 worker 路由”的组合模型。

#### Scenario: 仅本地后端
- **WHEN** 未启用远端 worker
- **THEN** 系统继续按现有 `cpu/vulkan` 逻辑选择本地 ASR

#### Scenario: 启用远端 worker
- **WHEN** 已配置远端 worker 且健康检查通过
- **THEN** 系统先尝试远端 ASR
- **AND** 失败时回落到本地 `cpu` 后端

## REMOVED Requirements
### Requirement: 自动局域网扫描发现首版落地
**Reason**: 在当前长期稳定优先的目标下，端口扫描不等于节点 ready，容易在 Windows 家庭局域网环境中误判。
**Migration**: 首版改用显式 endpoint + 健康检查；后续如有需要，再把自动发现作为辅助能力追加。
