# OPC Agent Platform

一个基于官方 `a2a-sdk==1.1.0` 的可运行 MVP。四个 OPC Agent 共享同一运行时，但拥有独立 Agent Card、A2A JSON-RPC 地址和任务存储。三轮沟通与最终报告可由本机 Ollama 或 DeepSeek 生成，协议身份和隐私边界由服务端控制。

## 当前能力

- 通过 Agent Card 发现每个 OPC Agent 的能力
- 真实执行三轮双向 A2A 消息与完成态 Task
- 每轮返回 `application/json` Artifact，并保留 task ID 和原始协议记录
- 基于项目方向、能力供需、协作方式、时间与 AI 用量生成匹配报告
- 联系方式、报价、合同等字段不会进入自动沟通
- 双方分别确认后才进入 `MUTUAL_APPROVED`
- 同一服务提供 API、现有视频原型和视频文件
- 报告返回实际模型、调用次数与 token 用量
- 两个 Agent 可通过 A2A 互查日历并生成暂定会面
- 日历工具只返回可用状态和备选时间，不暴露私人日程详情
- 可从本机 OPC Agent 向陌生第三方公网 Perkoon Agent 发起一次真实 A2A Task
- 两个 Agent 可通过 `opc.employee_chat.v1` 交替多轮沟通并累积共享上下文
- 调试记录包含 Agent Card URL、JSON-RPC URL、Task ID、请求响应和上下文前后快照

## 启动

```bash
uv sync --dev
uv run opc-agent-platform
```

模型配置从项目内 `.env` 加载；字段模板见 `.env.example`。使用 DeepSeek 且没有配置 API key 时，系统自动使用确定性规则引擎，便于离线开发。

打开 <http://127.0.0.1:8010/>。API 文档位于 <http://127.0.0.1:8010/docs>。

## 验证

```bash
uv run pytest
```

当前存储为进程内内存，适合协议与产品流程验证；生产环境需要替换为持久数据库、身份认证、字段级授权和 Agent Card 签名验证。

## 本地模型

本机开发推荐使用 Ollama 和 `qwen3:4b`。只需下载一个模型：两个 Agent 共用同一个 Ollama 模型服务，并通过各自的公开档案、角色提示和会话上下文形成不同回复。

```bash
brew services start ollama
ollama pull qwen3:4b
```

在 `.env` 中配置：

```dotenv
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3:4b
```

启动后访问 `/health`，确认 `decisionEngine` 为 `ollama`、`model` 为 `qwen3:4b`。要切回云模型，将 `LLM_PROVIDER` 改为 `deepseek` 并保留有效的 `DEEPSEEK_API_KEY`。

## 公网 A2A 演示

页面入口：<http://127.0.0.1:8010/app/> 的 `公网` 标签。

默认目标是陌生第三方 `https://perkoon.com`，无需 API Key。它会返回 P2P 文件传输 Agent 的能力说明、CLI/MCP 用法和后续动作。

API 入口：

```bash
curl -X POST http://127.0.0.1:8010/api/internet-a2a/demo \
  -H 'Content-Type: application/json' \
  -d '{
    "targetId": "perkoon",
    "prompt": "请介绍你是谁、能做什么，以及我的个人网站 Agent 下一步怎么与你协作。"
  }'
```

如果启动时设置了 HTTPS 的 `OPC_PUBLIC_BASE_URL`，例如临时隧道地址，页面还会额外提供 `shen-zhiye-public` 作为自有公网 Agent 备选：

```bash
OPC_PUBLIC_BASE_URL=https://your-tunnel.example uv run uvicorn opc_agent_platform.app:app --host 127.0.0.1 --port 8010
```

这个备选目标会通过公网 Agent Card 发现 `沈知野的 OPC Agent`，再发送结构化 `opc.public_inquiry.v1` JSON payload，返回中文公开资料回复和远端 Task ID。

```bash
curl -X POST http://127.0.0.1:8010/api/internet-a2a/demo \
  -H 'Content-Type: application/json' \
  -d '{
    "targetId": "shen-zhiye-public",
    "prompt": "你是谁？请用一句话回答。"
  }'
```

默认仍保留 `https://aureliusagent.dev` 作为外部 runtime 备选。它可以证明公网 A2A Task 完成，但返回内容偏运行时确认。目标只允许来自服务端白名单，避免前端把平台变成任意 URL 请求器。

## 双 Agent 对话调试

打开页面的 `公网` 标签，切换到 `双 Agent 调试`。陈默 Agent 与沈知野 Agent 拥有独立身份和角色提示，通过 A2A `message/send` 交替聊天。每轮请求会携带最近聊天历史，因此两个 Agent 能像普通 AI 聊天窗口一样理解上下文并自然接续。

当前页面会创建两个带独立访问令牌的长期会话链接，分别对应用户 A 和用户 B。创建后及沟通过程中都可以切换控制模式，建议把两个链接放在两个浏览器窗口、无痕窗口或两台设备中打开：

- `逐条人工批准`：用户 A 和用户 B 分别审核自己 Agent 的草稿，批准后才发送。
- `Agent 托管`：先创建并打开沟通窗口；任一参与用户确认目标并点击开始后，本地模型才自动生成和发送下一轮。
- `人工直聊`：双方本人直接发送消息，模型不介入；之后可把完整聊天历史交回 Agent 继续。

- 用户 A 只能查看和批准陈默 Agent 的待发送草稿。
- 用户 B 只能查看和批准沈知野 Agent 的待发送草稿。
- 草稿可以由本人编辑，也可以拒绝并结束会话。
- 未批准草稿及其 A2A Artifact 对另一方不可见。
- 批准后，消息、共享上下文和协议证据通过 SSE 自动同步到两个页面。
- 每个批准请求携带会话版本，重复点击或过期页面不能覆盖新状态。
- 启动后的自动接管由后端运行，不要求两个页面保持打开；任一用户都可以停止。停止时已经发出的请求可能完成，但不会继续发送下一轮。
- Agent 托管和人工审核中的 Agent 回复都会创建独立 A2A Task，并保留共享上下文、Task ID、请求、响应和审计事件；人工直聊记录消息与审计，但不伪造 A2A Task。
- 默认 `runPolicy=CONTINUOUS`，不限制会话总轮数。Agent 没有新的有效内容或检测到重复时会自动暂停；用户可以人工插话后继续托管。测试时仍可传 `maxTurns` 使用限定轮数策略。

本机调试时，公开基址必须和实际监听地址一致。例如使用 `8012`：

```bash
OPC_PUBLIC_BASE_URL=http://127.0.0.1:8012 \
  uv run uvicorn opc_agent_platform.app:app --host 127.0.0.1 --port 8012
```

直接调用：

```bash
curl -X POST http://127.0.0.1:8012/api/employee-chats \
  -H 'Content-Type: application/json' \
  -d '{
    "fromAgentId": "opc-builder",
    "toAgentId": "shen-zhiye",
    "goal": "验证两个 Agent 能否共享上下文并形成可执行下一步",
    "maxTurns": 4
  }'
```

要切换为两个真正独立的公网部署，给编排服务配置两个 Agent 根地址。地址必须能返回各自的 `/.well-known/agent-card.json`：

```bash
OPC_EMPLOYEE_AGENT_URL_OPC_BUILDER=https://agent-a.example/a2a/opc-builder \
OPC_EMPLOYEE_AGENT_URL_SHEN_ZHIYE=https://agent-b.example/a2a/shen-zhiye \
OPC_PUBLIC_BASE_URL=https://orchestrator.example \
  uv run uvicorn opc_agent_platform.app:app --host 0.0.0.0 --port 8010
```

使用 Ollama 或配置 DeepSeek 后，两个 Agent 会分别根据自己的公开档案、对方消息和当前共享上下文生成回复。使用 DeepSeek 但没有 `DEEPSEEK_API_KEY` 时会回退到确定性规则。无论哪种模式，每轮仍是独立 A2A Task，不会退化为前端模拟聊天。

## 时间确认任务

下面的请求会让两个 Agent 依次执行 `A -> B`、`B -> A`、`A -> B` 三轮确认：

```bash
curl -X POST http://127.0.0.1:8010/api/schedule-inquiries \
  -H 'Content-Type: application/json' \
  -d '{
    "fromAgentId": "opc-builder",
    "toAgentId": "shen-zhiye",
    "requestedStart": "2026-08-10T15:00:00+08:00",
    "durationMinutes": 30,
    "topic": "一起沟通 OPC Agent 合作"
  }'
```

双方 Agent 都可用时，状态进入 `WAITING_HUMAN_CONFIRMATION`。只有两位本人分别调用确认接口后，状态才会变成 `CONFIRMED`。
