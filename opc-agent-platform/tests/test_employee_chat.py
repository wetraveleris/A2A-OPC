import json

import httpx
import pytest

from opc_agent_platform.app import create_app


@pytest.mark.asyncio
async def test_two_agents_chat_over_a2a_with_accumulated_context() -> None:
    app = create_app(base_url="http://testserver", use_environment_llm=False)
    transport = httpx.ASGITransport(app=app)
    app.state.employee_chat_service.communicator.transport = transport

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        card = await client.get(
            "/a2a/shen-zhiye/.well-known/agent-card.json"
        )
        response = await client.post(
            "/api/employee-chats",
            json={
                "fromAgentId": "opc-builder",
                "toAgentId": "shen-zhiye",
                "goal": "一起验证个人网站 Agent 能否自动找到合作机会",
                "maxTurns": 4,
            },
        )

    assert card.status_code == 200
    assert "employee_chat" in [skill["id"] for skill in card.json()["skills"]]
    assert response.status_code == 201, response.text
    chat = response.json()
    assert chat["state"] == "COMPLETED"
    assert chat["protocol"] == "opc.employee_chat.v1"
    assert len(chat["turns"]) == 4
    assert [turn["toAgentId"] for turn in chat["turns"]] == [
        "shen-zhiye",
        "opc-builder",
        "shen-zhiye",
        "opc-builder",
    ]
    assert len({turn["taskId"] for turn in chat["turns"]}) == 4
    assert {turn["taskState"] for turn in chat["turns"]} == {
        "TASK_STATE_COMPLETED"
    }
    assert all(turn["jsonrpcMethod"] == "message/send" for turn in chat["turns"])
    assert chat["turns"][0]["agentCardUrl"].endswith(
        "/a2a/shen-zhiye/.well-known/agent-card.json"
    )
    assert chat["turns"][2]["request"]["sharedContext"]["decisions"] == [
        "先用一个小实验验证合作价值"
    ]
    assert "先进行两周试合作，双方本人再确认正式承诺" in chat["context"][
        "decisions"
    ]

    serialized = json.dumps(chat, ensure_ascii=False).lower()
    for private_marker in (
        "private-demo",
        "private-shen",
        "13800000001",
        "demo-owner@example.com",
    ):
        assert private_marker.lower() not in serialized


@pytest.mark.asyncio
async def test_employee_chat_record_can_be_reloaded_for_debugging() -> None:
    app = create_app(base_url="http://testserver", use_environment_llm=False)
    transport = httpx.ASGITransport(app=app)
    app.state.employee_chat_service.communicator.transport = transport

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        created = await client.post(
            "/api/employee-chats",
            json={
                "goal": "确认两个 Agent 是否能共享对话上下文",
                "maxTurns": 2,
            },
        )
        loaded = await client.get(
            f"/api/employee-chats/{created.json()['id']}"
        )

    assert loaded.status_code == 200
    assert loaded.json() == created.json()
