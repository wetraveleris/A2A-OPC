import json

import httpx
import pytest

from opc_agent_platform.app import create_app


PRIVATE_MARKERS = {
    "private-demo",
    "private-shen",
    "13800000001",
    "demo-owner@example.com",
}


@pytest.mark.asyncio
async def test_three_round_screening_uses_completed_a2a_tasks() -> None:
    app = create_app(base_url="http://testserver", use_environment_llm=False)
    transport = httpx.ASGITransport(app=app)
    app.state.screening_service.communicator.transport = transport

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        card_response = await client.get(
            "/a2a/shen-zhiye/.well-known/agent-card.json"
        )
        assert card_response.status_code == 200
        assert card_response.json()["supportedInterfaces"][0]["protocolVersion"] == "1.0"

        response = await client.post(
            "/api/screenings",
            json={
                "fromAgentId": "opc-builder",
                "toAgentId": "shen-zhiye",
            },
        )

    assert response.status_code == 201, response.text
    screening = response.json()
    assert screening["state"] == "WAITING_OWNER_APPROVAL"
    assert len(screening["transcript"]) == 3
    assert [turn["round"] for turn in screening["transcript"]] == [1, 2, 3]
    assert {turn["taskState"] for turn in screening["transcript"]} == {
        "TASK_STATE_COMPLETED"
    }
    assert len(screening["report"]["evidenceTaskIds"]) == 3
    assert screening["report"]["recommendation"] == "WORTH_MEETING"

    serialized = json.dumps(screening, ensure_ascii=False).lower()
    for marker in PRIVATE_MARKERS:
        assert marker.lower() not in serialized
    for forbidden_key in ("email", "phone", "wechat", "pricing", "contract"):
        assert f'"{forbidden_key}"' not in serialized


@pytest.mark.asyncio
async def test_approval_requires_both_people() -> None:
    app = create_app(base_url="http://testserver", use_environment_llm=False)
    transport = httpx.ASGITransport(app=app)
    app.state.screening_service.communicator.transport = transport

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        screening = (
            await client.post(
                "/api/screenings",
                json={
                    "fromAgentId": "opc-builder",
                    "toAgentId": "shen-zhiye",
                },
            )
        ).json()
        first = await client.post(
            f"/api/screenings/{screening['id']}/approve",
            json={"agentId": "opc-builder", "decision": "approve"},
        )
        second = await client.post(
            f"/api/screenings/{screening['id']}/approve",
            json={"agentId": "shen-zhiye", "decision": "approve"},
        )

    assert first.json()["state"] == "WAITING_REMOTE_APPROVAL"
    assert second.json()["state"] == "MUTUAL_APPROVED"
