import httpx
import pytest

from opc_agent_platform.app import create_app
from opc_agent_platform.conversation import A2ATaskTextResult


@pytest.mark.asyncio
async def test_internet_a2a_demo_dispatches_to_third_party_perkoon_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPC_REMOTE_AGENT_B_URL", raising=False)
    app = create_app(base_url="http://testserver", use_environment_llm=False)
    dispatched: dict[str, str] = {}

    async def fake_send(agent_url: str, prompt: str) -> A2ATaskTextResult:
        dispatched["agent_url"] = agent_url
        dispatched["prompt"] = prompt
        return A2ATaskTextResult(
            task_id="remote-task-42",
            task_state="TASK_STATE_COMPLETED",
            text="Remote Agent accepted the A2A request.",
        )

    app.state.internet_a2a_service.communicator.send_text_to_url = fake_send
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        targets = await client.get("/api/internet-a2a/targets")
        response = await client.post(
            "/api/internet-a2a/demo",
            json={
                "targetId": "perkoon",
                "prompt": "请介绍你是谁，以及怎么和我的个人网站 Agent 协作。",
            },
        )

    assert targets.status_code == 200
    assert targets.json()[0]["name"] == "Perkoon Agent"
    assert targets.json()[0]["protocolVersion"] == "0.3.0"
    assert response.status_code == 201, response.text
    record = response.json()
    assert record["taskId"] == "remote-task-42"
    assert record["taskState"] == "TASK_STATE_COMPLETED"
    assert record["responseText"] == "Remote Agent accepted the A2A request."
    assert record["targetUrl"] == "https://perkoon.com"
    assert dispatched["agent_url"] == "https://perkoon.com"
    assert "OPC Link personal website Agent" in dispatched["prompt"]
    assert "请介绍你是谁" in dispatched["prompt"]


@pytest.mark.asyncio
async def test_internet_a2a_demo_rejects_unknown_target() -> None:
    app = create_app(base_url="http://testserver", use_environment_llm=False)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/internet-a2a/demo",
            json={"targetId": "arbitrary-url", "prompt": "hello"},
        )

    assert response.status_code == 404
    assert "Unknown internet A2A target" in response.json()["detail"]


@pytest.mark.asyncio
async def test_computer_b_target_uses_employee_chat_and_returns_model_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "OPC_REMOTE_AGENT_B_URL",
        "https://example-tunnel.loca.lt/agent-b/a2a/shen-zhiye",
    )
    app = create_app(use_environment_llm=False)
    dispatched: dict[str, object] = {}

    async def fake_send_json(
        agent_url: str,
        payload: dict[str, object],
    ) -> tuple[str, str, dict[str, object]]:
        dispatched["agent_url"] = agent_url
        dispatched["payload"] = payload
        return (
            "public-opc-task-1",
            "TASK_STATE_COMPLETED",
            {
                "reply": "我是沈知野，一个独立开发者。",
                "debug": {
                    "decisionEngine": {
                        "provider": "ollama",
                        "model": "qwen3:1.7b",
                    },
                },
            },
        )

    app.state.internet_a2a_service.communicator.send_json_to_url = fake_send_json
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        targets = await client.get("/api/internet-a2a/targets")
        response = await client.post(
            "/api/internet-a2a/demo",
            json={
                "targetId": "computer-b",
                "prompt": "你是谁？请用一句话回答。",
            },
        )

    assert targets.status_code == 200
    assert targets.json()[0]["id"] == "computer-b"
    assert any(target["id"] == "computer-b" for target in targets.json())
    assert response.status_code == 201, response.text
    assert response.json()["responseText"] == "我是沈知野，一个独立开发者。"
    assert response.json()["remoteProvider"] == "ollama"
    assert response.json()["remoteModel"] == "qwen3:1.7b"
    assert dispatched["agent_url"] == (
        "https://example-tunnel.loca.lt/agent-b/a2a/shen-zhiye"
    )
    payload = dispatched["payload"]
    assert isinstance(payload, dict)
    assert payload["protocol"] == "opc.employee_chat.v1"
    assert payload["senderAgentId"] == "opc-builder"
    assert payload["recipientAgentId"] == "shen-zhiye"
    assert payload["message"] == "你是谁？请用一句话回答。"
    assert payload["sharedContext"]["goal"] == "回答电脑 A 用户发来的问题"
