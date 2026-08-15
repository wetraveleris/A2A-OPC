# OPC Agent Platform

一个基于官方 `a2a-sdk==1.1.0` 的 OPC Agent 产品原型。平台提供账号、个人空间、作品发现、好友连接和可审计的 A2A 沟通；本机 Ollama 或 DeepSeek 负责 Agent 回复，协议身份和隐私边界由服务端控制。

## 当前能力

- 通过 Agent Card 发现每个 OPC Agent 的能力
- 真实执行三轮双向 A2A 消息与完成态 Task
- 每轮返回 `application/json` Artifact，并保留 task ID 和原始协议记录
- 基于项目方向、能力供需、协作方式、时间与 AI 用量生成匹配报告
- 联系方式、报价、合同等字段不会进入自动沟通
- 双方分别确认后才进入 `MUTUAL_APPROVED`
- 同一服务提供 API 和纯文字人物名片应用，不托管图片或视频媒体
- 报告返回实际模型、调用次数与 token 用量
- 账号密码登录、个人资料编辑和本机在线 Agent 绑定
- 作品管理支持公开、好友可见和私密三种范围
- 连接关系需要双方同意，连接页只展示已建立关系和设备状态
- 发现流只返回已绑定到其他账号的在线 Relay Agent 名片，不返回邮箱等账号隐私
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

账号和资料默认存储在 `data/opc-link.db`（SQLite），生产环境通过 `OPC_DATABASE_URL` 使用 PostgreSQL。首轮使用 SQLAlchemy 自动建表；正式上线前应使用 Alembic 迁移以及 Redis 承载会话和事件。产品不上传或托管图片、视频等媒体。

## 账号与产品 API

网页入口现在先要求登录或注册。核心接口：

```text
POST /api/auth/register
POST /api/auth/login
GET  /api/auth/me
GET  /api/me/agent-devices
POST /api/me/agent-devices/claim
GET  /api/discovery/online-agents
POST /api/agent-introductions
GET  /api/agent-introductions/{introduction_id}
POST /api/agent-introductions/{introduction_id}/request-contact
GET  /api/me/profile
PUT  /api/me/profile
GET/POST /api/me/works
GET  /api/connections
GET/POST /api/connection-requests
```

认证使用 `HttpOnly`、`SameSite=Lax` 的 `opc_session` Cookie。账号只能绑定一个当前在线且尚未被其他账号绑定的 Relay Agent；发现流不会展示自己的 Agent，也不会展示离线或没有账号归属的 Agent。

经典认识流程为：查看在线 Agent 人物名片 → 点击“让 Agent 先了解” → 两台电脑通过公网 Relay 执行三次真实 A2A 1.0 Task → 查看双方介绍和匹配结果 → 请求建立联系 → 对方本人接受 → 双方进入连接列表。三次 Task ID、状态和对话内容会持久化，并显示在连接历史中；建立连接后可继续使用人工直聊、人工审核或 Agent 托管。

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

公网 A2A 能力保留为开发调试入口和 API；生产主导航是 `发现 / 连接 / 我的`。发现流面向陌生用户的纯文字人物名片，双 Agent 调试页仍用于验证人工介入、Agent 托管和真实公网 Relay。

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

登录后进入 `我的 → 开发调试：公网 A2A`，切换到 `双 Agent 调试`。陈默 Agent 与沈知野 Agent 拥有独立身份和角色提示，通过 A2A `message/send` 交替聊天。每轮请求会携带最近聊天历史，因此两个 Agent 能像普通 AI 聊天窗口一样理解上下文并自然接续。

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

### 出站 Relay 双电脑连接

`RELAY_A_B` 模式不要求两台电脑开放入站端口，也不依赖反向 SSH。两台电脑分别运行本地 A2A Runtime 和 `opc-relay-node`，Node 主动通过 WebSocket 连接公网 Relay。Relay 只转发任务和结果，本地 Ollama 不暴露到公网。

Relay 所在服务与两个 Node 必须使用同一个随机 Token：

```dotenv
OPC_RELAY_URL=wss://your-domain.example/api/relay/ws
OPC_RELAY_TOKEN=replace-with-a-long-random-token
```

电脑 A：

```dotenv
LLM_PROVIDER=ollama
OLLAMA_MODEL=qwen3:4b
OPC_NODE_AGENT_ID=opc-builder
OPC_LOCAL_AGENT_URL=http://127.0.0.1:8012/a2a/opc-builder
```

电脑 B：

```dotenv
LLM_PROVIDER=ollama
OLLAMA_MODEL=qwen3:1.7b
OPC_NODE_AGENT_ID=shen-zhiye
OPC_LOCAL_AGENT_URL=http://127.0.0.1:8010/a2a/shen-zhiye
```

两台电脑在各自的 Agent Runtime 启动后运行：

```bash
uv run opc-relay-node
```

访问 `/api/relay/agents` 可以查看两端在线状态和各自模型。页面的 `公网 → 双 Agent 调试` 默认使用 Relay；逐条人工批准和 Agent 托管要求两端在线，人工直聊不调用模型，允许离线创建。
