import json

from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from opc_agent_platform.app import create_app
from opc_agent_platform.calendar_tool import check_availability
from opc_agent_platform.models import AvailabilityStatus


SHANGHAI = ZoneInfo("Asia/Shanghai")
TODAY_AT_THREE = datetime(2026, 8, 10, 15, 0, tzinfo=SHANGHAI)


def test_calendar_tool_keeps_private_blocks_private() -> None:
    shen = check_availability("shen-zhiye", TODAY_AT_THREE, 30)
    lin = check_availability("lin-yu", TODAY_AT_THREE, 30)

    assert shen.status == AvailabilityStatus.AVAILABLE
    assert shen.alternatives == []
    assert lin.status == AvailabilityStatus.BUSY
    assert lin.alternatives


@pytest.mark.asyncio
async def test_agents_mutually_confirm_today_at_three_over_a2a() -> None:
    app = create_app(base_url="http://testserver", use_environment_llm=False)
    transport = httpx.ASGITransport(app=app)
    app.state.screening_service.communicator.transport = transport

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/schedule-inquiries",
            json={
                "fromAgentId": "opc-builder",
                "toAgentId": "shen-zhiye",
                "requestedStart": TODAY_AT_THREE.isoformat(),
                "durationMinutes": 30,
                "topic": "一起沟通 OPC Agent 合作",
            },
        )

        assert response.status_code == 201, response.text
        schedule = response.json()
        assert schedule["state"] == "WAITING_HUMAN_CONFIRMATION"
        assert schedule["agentsConfirmed"] is True
        assert schedule["humanConfirmationRequired"] is True
        assert len(schedule["transcript"]) == 3
        assert [
            (turn["fromAgentId"], turn["toAgentId"])
            for turn in schedule["transcript"]
        ] == [
            ("opc-builder", "shen-zhiye"),
            ("shen-zhiye", "opc-builder"),
            ("opc-builder", "shen-zhiye"),
        ]
        assert {turn["taskState"] for turn in schedule["transcript"]} == {
            "TASK_STATE_COMPLETED"
        }
        assert {
            turn["response"]["availability"]["status"]
            for turn in schedule["transcript"]
        } == {"AVAILABLE"}
        assert {
            turn["response"]["decisionEngine"]["provider"]
            for turn in schedule["transcript"]
        } == {"calendar_tool"}

        first_confirmation = await client.post(
            f"/api/schedule-inquiries/{schedule['id']}/confirm",
            json={"agentId": "opc-builder", "decision": "confirm"},
        )
        second_confirmation = await client.post(
            f"/api/schedule-inquiries/{schedule['id']}/confirm",
            json={"agentId": "shen-zhiye", "decision": "confirm"},
        )

    assert first_confirmation.json()["state"] == "WAITING_HUMAN_CONFIRMATION"
    assert second_confirmation.json()["state"] == "CONFIRMED"
    serialized = json.dumps(schedule, ensure_ascii=False).lower()
    for marker in (
        "private-demo",
        "private-shen",
        "13800000001",
        "demo-owner@example.com",
    ):
        assert marker.lower() not in serialized
