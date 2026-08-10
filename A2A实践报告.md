# Google A2A 协议分析与本地实践

> 核对日期：2026-08-09（Asia/Shanghai）

## 1. 当前项目状态

A2A（Agent2Agent）由 Google 于 2025 年发起，现已贡献给 Linux Foundation，官方 GitHub 组织为 `a2aproject`。因此今天所说的“Google A2A”应理解为“Google 发起、Linux Foundation 治理的 A2A 开源协议”。

- 协议仓库：<https://github.com/a2aproject/A2A>
- 最新仓库 Release：`v1.0.1`（2026-05-28）
- 当前正式协议版本：`1.0`
- 官方 Python SDK：<https://github.com/a2aproject/a2a-python>
- Python SDK 最新 Release：`v1.1.2`（2026-07-22）
- 官方示例：<https://github.com/a2aproject/a2a-samples>
- 许可证：Apache-2.0

注意：协议仓库的 `v1.0.1` 是项目 Release 标签，Agent Card 和请求头中协商的协议版本仍为 `1.0`。`main` 代表持续开发状态，生产实现应以已发布规范及 SDK 的兼容性表为准。

## 2. A2A 解决什么问题

A2A 让不同厂商、不同框架、不同语言实现的“自主 Agent 应用”通过统一协议协作，同时不要求对方公开内部记忆、推理过程或工具实现。

它的核心不是模型调用，也不是工具协议，而是 Agent 应用之间的互操作：

1. 发现：客户端通过 Agent Card 获得身份、技能、输入输出类型、接口和安全要求。
2. 协商：客户端从 `supportedInterfaces` 中选择双方支持的协议绑定。
3. 交互：客户端发送 `Message`，服务端可直接返回 `Message`，也可创建可追踪的 `Task`。
4. 长任务：通过轮询、流式订阅或 Push Notification 接收进度和结果。
5. 多轮：非终态任务可以继续接收消息，支持 `INPUT_REQUIRED` 和 `AUTH_REQUIRED` 等中断状态。

## 3. 协议结构

规范分三层：

- 数据模型：`AgentCard`、`Message`、`Part`、`Task`、`Artifact`、`Extension`。
- 抽象操作：发送消息、流式发送、查询/列举/取消/订阅任务、Push Notification 配置、扩展卡片。
- 传输绑定：JSON-RPC、gRPC、HTTP+JSON/REST；同一 Agent 的不同绑定必须提供语义等价的能力。

规范的权威数据定义是 `A2A/specification/a2a.proto`，JSON 字段统一使用 camelCase。

### 关键对象

- `AgentCard`：Agent 的“服务说明书”，标准发现路径为 `/.well-known/agent-card.json`。
- `Message`：一次用户或 Agent 消息，由一个或多个 `Part` 组成。
- `Part`：最小内容单元，可承载文本、文件或结构化数据。
- `Task`：有状态、可持续、可查询的工作单元。
- `Artifact`：Agent 产出的最终或增量结果。
- `contextId`：将相关 Task/Message 归入同一会话上下文。

### 任务状态

常见状态流为：

```text
SUBMITTED -> WORKING -> COMPLETED
                     -> FAILED / CANCELED / REJECTED
          -> INPUT_REQUIRED
          -> AUTH_REQUIRED
```

终态任务不能继续接收消息。`INPUT_REQUIRED` 和 `AUTH_REQUIRED` 是可恢复的中断状态，适合人机协作和外部授权。

### 三种更新方式

- 同步/阻塞：`SendMessage` 默认等待终态或中断态，简单可靠。
- 流式：通过 SSE（HTTP 绑定）或服务端流（gRPC）接收状态与 Artifact 增量。
- 异步 Push：客户端注册 webhook，适合断线或超长任务；必须处理 SSRF、鉴权、重放和 URL 校验。

## 4. A2A 与 MCP 的边界

- MCP 侧重“模型/Agent 如何调用工具和读取资源”。
- A2A 侧重“一个独立 Agent 应用如何发现并委派给另一个独立 Agent 应用”。
- 二者互补：一个 A2A Agent 内部可以使用 MCP 调工具，但不应把另一个有自主任务生命周期的 Agent 简化成普通工具。

## 5. 本地下载内容

```text
A2A平台/
├── A2A/                 # 官方协议、规范文档、Proto 定义
├── a2a-python/          # 官方 Python SDK 源码
├── a2a-samples/         # 官方多语言示例
├── practice/
│   └── raw_client.py    # 本次编写的零第三方依赖 JSON-RPC 客户端
└── A2A实践报告.md
```

三个官方仓库均按当前 `main` 浅克隆，具体提交可用以下命令确认：

```bash
git -C A2A rev-parse HEAD
git -C a2a-python rev-parse HEAD
git -C a2a-samples rev-parse HEAD
```

## 6. 已完成的实践

使用官方 Python `helloworld` Agent，Python 3.13 隔离环境和样例固定的 `a2a-sdk==1.1.0`。

### 自动化验证

```bash
cd a2a-samples/samples/python/agents/helloworld
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/pytest -s -q test_client.py
```

实际结果：`1 passed`。覆盖以下路径：

- 获取公开 Agent Card
- 非流式 `SendMessage`
- 流式调用，依次收到 `SUBMITTED`、`WORKING`、Artifact、`COMPLETED`
- 获取扩展 Agent Card

### 启动服务

```bash
cd a2a-samples/samples/python/agents/helloworld
.venv/bin/python __main__.py
```

服务地址：`http://127.0.0.1:9999`

### 不使用客户端 SDK 发送请求

另开终端，在工作区根目录执行：

```bash
python3 practice/raw_client.py "你好，A2A"
```

该客户端只使用 Python 标准库，先读取 Agent Card，再选择其 JSON-RPC 接口并调用 `SendMessage`。本次实际返回包含：

- `TASK_STATE_COMPLETED`
- 独立的 `id`、`contextId` 和 `artifactId`
- 用户/Agent 消息历史
- `text/plain` Artifact

## 7. 生产落地判断

A2A 1.0 已具备较稳定的核心抽象、多绑定和官方多语言 SDK，适合异构 Agent 的服务间互操作。但协议只定义交互契约，不替你解决 Agent 质量、身份系统、租户隔离、数据治理和业务补偿。

上线前至少要补齐：

- HTTPS/TLS、服务身份验证、OAuth/API Key 与最小权限授权。
- Agent Card、消息、Artifact 全部按不可信输入处理，防止提示注入和内容注入。
- Task 持久化、幂等、超时、取消、重试、限流和审计；不能使用样例的内存 Task Store。
- Push webhook 的 SSRF 防护、回调鉴权、重放防护和网络出口限制。
- 对声明能力、媒体类型、协议版本和扩展做客户端预检查。
- 统一 trace/correlation ID，并把 A2A Task 与内部工作流实例关联。

## 8. 推荐的下一步实践

将 `HelloWorldAgent` 换成一个真实但低风险的领域 Agent，并至少增加：任务持久化、鉴权、一个 `INPUT_REQUIRED` 多轮流程、一个 SSE 增量 Artifact，以及另一个不同语言 SDK 实现的客户端。这样才能验证 A2A 的核心价值，而不只是单进程连通性。
