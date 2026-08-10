import json

from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from opc_agent_platform.app import create_app


SHANGHAI = ZoneInfo("Asia/Shanghai")
TODAY_AT_THREE = datetime(2026, 8, 10, 15, 0, tzinfo=SHANGHAI)


def _events(body: str) -> list[tuple[str, dict[str, object]]]:
    parsed: list[tuple[str, dict[str, object]]] = []
    for frame in body.strip().split("\n\n"):
        lines = frame.splitlines()
        event = next(line[7:] for line in lines if line.startswith("event: "))
        data = next(line[6:] for line in lines if line.startswith("data: "))
        parsed.append((event, json.loads(data)))
    return parsed


@pytest.mark.asyncio
async def test_live_schedule_is_streamed_through_a2a_artifact_updates() -> None:
    app = create_app(base_url="http://testserver", use_environment_llm=False)
    transport = httpx.ASGITransport(app=app)
    app.state.live_conversation_service.communicator.transport = transport

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        async with client.stream(
            "POST",
            "/api/live-conversations/schedule",
            json={
                "fromAgentId": "opc-builder",
                "toAgentId": "shen-zhiye",
                "requestedStart": TODAY_AT_THREE.isoformat(),
                "durationMinutes": 30,
                "topic": "一起沟通 OPC Agent 合作",
            },
        ) as response:
            body = "".join([chunk async for chunk in response.aiter_text()])

        assert response.status_code == 201
        assert response.headers["content-type"].startswith("text/event-stream")
        events = _events(body)
        assert sum(name == "message.delta" for name, _ in events) > 8
        assert sum(name == "message.started" for name, _ in events) == 4
        assert sum(name == "message.completed" for name, _ in events) == 4

        final = next(data for name, data in events if name == "conversation.completed")
        conversation_id = str(final["id"])
        assert final["state"] == "WAITING_HUMAN_CONFIRMATION"
        assert final["agentsConfirmed"] is True
        assert [message["speakerAgentId"] for message in final["messages"]] == [
            "opc-builder",
            "shen-zhiye",
            "opc-builder",
            "shen-zhiye",
        ]
        assert {message["taskState"] for message in final["messages"]} == {
            "TASK_STATE_COMPLETED"
        }

        confirmation = await client.post(
            f"/api/live-conversations/{conversation_id}/confirm",
            json={"agentId": "opc-builder", "decision": "confirm"},
        )

    assert confirmation.status_code == 200
    assert confirmation.json()["state"] == "WAITING_HUMAN_CONFIRMATION"
    assert confirmation.json()["confirmations"] == ["opc-builder"]
    serialized = json.dumps(final, ensure_ascii=False).lower()
    for marker in (
        "private-demo",
        "private-shen",
        "13800000001",
        "demo-owner@example.com",
    ):
        assert marker.lower() not in serialized


@pytest.mark.asyncio
async def test_live_schedule_does_not_claim_confirmation_when_a_calendar_is_busy() -> None:
    app = create_app(base_url="http://testserver", use_environment_llm=False)
    transport = httpx.ASGITransport(app=app)
    app.state.live_conversation_service.communicator.transport = transport

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        async with client.stream(
            "POST",
            "/api/live-conversations/schedule",
            json={
                "fromAgentId": "opc-builder",
                "toAgentId": "lin-yu",
                "requestedStart": TODAY_AT_THREE.isoformat(),
                "durationMinutes": 30,
                "topic": "一起沟通 OPC Agent 合作",
            },
        ) as response:
            body = "".join([chunk async for chunk in response.aiter_text()])

    assert response.status_code == 201
    final = next(data for name, data in _events(body) if name == "conversation.completed")
    assert final["state"] == "NO_COMMON_SLOT"
    assert final["agentsConfirmed"] is False
    assert len(final["messages"]) == 2
