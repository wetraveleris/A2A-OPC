from __future__ import annotations

import httpx
import pytest

from opc_agent_platform.app import create_app


@pytest.mark.asyncio
async def test_online_agents_create_a2a_introduction_and_contact(tmp_path) -> None:
    app = create_app(
        base_url="http://testserver",
        use_environment_llm=False,
        database_url=f"sqlite:///{tmp_path / 'agent-network.db'}",
    )
    transport = httpx.ASGITransport(app=app)
    communicator = app.state.screening_service.communicator
    communicator.transport = transport
    relay_hub = app.state.relay_hub

    relay_hub.is_online = lambda agent_id: agent_id in {"opc-builder", "shen-zhiye"}
    relay_hub.status = lambda agent_ids: [
        {
            "agentId": agent_id,
            "online": relay_hub.is_online(agent_id),
            "connectedAt": "2026-08-15T00:00:00+00:00",
            "lastSeen": "2026-08-15T00:00:00+00:00",
            "metadata": {
                "provider": "ollama",
                "model": "qwen3:4b" if agent_id == "opc-builder" else "qwen3:1.7b",
            },
        }
        for agent_id in agent_ids
    ]

    async def dispatch(agent_id, payload):
        return await communicator.send(agent_id, payload)

    relay_hub.dispatch = dispatch

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as alice, httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as bob:
        for client, username, display_name in (
            (alice, "alice-agent", "Alice"),
            (bob, "bob-agent", "Bob"),
        ):
            response = await client.post(
                "/api/auth/register",
                json={
                    "username": username,
                    "email": f"{username}@example.com",
                    "password": "correct horse battery phrase",
                    "displayName": display_name,
                },
            )
            assert response.status_code == 201, response.text

        claim_a = await alice.post(
            "/api/me/agent-devices/claim",
            json={"agentId": "opc-builder", "name": "A computer"},
        )
        claim_b = await bob.post(
            "/api/me/agent-devices/claim",
            json={"agentId": "shen-zhiye", "name": "B computer"},
        )
        assert claim_a.status_code == claim_b.status_code == 200

        discovery = await alice.get("/api/discovery/online-agents")
        assert discovery.status_code == 200, discovery.text
        assert [card["agentId"] for card in discovery.json()] == ["shen-zhiye"]
        assert discovery.json()[0]["online"] is True
        assert discovery.json()[0]["model"] == "qwen3:1.7b"

        introduced = await alice.post(
            "/api/agent-introductions",
            json={
                "targetAgentId": "shen-zhiye",
                "goal": "请双方介绍自己并判断是否值得建立联系。",
            },
        )
        assert introduced.status_code == 201, introduced.text
        introduction = introduced.json()
        assert introduction["state"] == "WAITING_APPROVAL"
        assert len(introduction["transcript"]) == 3
        assert len(
            {turn["taskId"] for turn in introduction["transcript"]}
        ) == 3
        assert all(
            turn["taskState"] == "TASK_STATE_COMPLETED"
            for turn in introduction["transcript"]
        )

        requested = await alice.post(
            f"/api/agent-introductions/{introduction['id']}/request-contact"
        )
        assert requested.status_code == 200, requested.text
        assert requested.json()["state"] == "CONTACT_REQUESTED"

        incoming = await bob.get("/api/connection-requests")
        request_record = next(
            item
            for item in incoming.json()
            if item["direction"] == "INCOMING" and item["status"] == "PENDING"
        )
        assert len(request_record["introduction"]["transcript"]) == 3

        accepted = await bob.post(
            f"/api/connection-requests/{request_record['id']}/accept"
        )
        assert accepted.status_code == 204

        connections = await alice.get("/api/connections")
        assert connections.status_code == 200
        assert connections.json()[0]["user"]["username"] == "bob-agent"
        history = connections.json()[0]["introductions"][0]
        assert history["state"] == "CONNECTED"
        assert len(history["transcript"]) == 3
