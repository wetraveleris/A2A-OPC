# OPC Agent Platform

一个基于官方 `a2a-sdk==1.1.0` 的可运行 MVP。四个 OPC Agent 共享同一运行时，但拥有独立 Agent Card、A2A JSON-RPC 地址和任务存储。三轮沟通与最终报告由 DeepSeek 生成，协议身份和隐私边界由服务端控制。

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

## 启动

```bash
uv sync --dev
uv run opc-agent-platform
```

DeepSeek 配置从项目内 `.env` 加载；字段模板见 `.env.example`。没有配置 API key 时，系统自动使用确定性规则引擎，便于离线开发。

打开 <http://127.0.0.1:8010/>。API 文档位于 <http://127.0.0.1:8010/docs>。

## 验证

```bash
uv run pytest
```

当前存储为进程内内存，适合协议与产品流程验证；生产环境需要替换为持久数据库、身份认证、字段级授权和 Agent Card 签名验证。

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
