from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select

from opc_agent_platform.app import create_app
from opc_agent_platform.database import AgentIntroduction, Connection, Device, User


def _room_access(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    return parse_qs(parsed.query)["room"][0], parse_qs(parsed.query)["token"][0]


@pytest.mark.asyncio
async def test_connected_user_can_open_room_with_introduction_context(tmp_path) -> None:
    app = create_app(
        base_url="http://testserver",
        use_environment_llm=False,
        database_url=f"sqlite:///{tmp_path / 'connection-chat.db'}",
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as alice, httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as bob:
        await alice.post(
            "/api/auth/register",
            json={
                "username": "chat-alice",
                "email": "chat-alice@example.com",
                "password": "alice secure password",
                "displayName": "Alice",
            },
        )
        await bob.post(
            "/api/auth/register",
            json={
                "username": "chat-bob",
                "email": "chat-bob@example.com",
                "password": "bob secure password",
                "displayName": "Bob",
            },
        )
        request = await alice.post(
            "/api/connection-requests",
            json={"targetUsername": "chat-bob", "message": "继续交流"},
        )
        request_id = request.json()["id"]
        assert (await bob.post(f"/api/connection-requests/{request_id}/accept")).status_code == 204

        me = app.state.account_service
        users = {}
        with me.database.session() as session:
            users["alice"] = session.scalar(select_user("chat-alice"))
            users["bob"] = session.scalar(select_user("chat-bob"))
            connection = session.scalar(
                select(Connection).where(
                    (Connection.user_a_id == users["alice"].id)
                    | (Connection.user_b_id == users["alice"].id)
                )
            )
            connection_id = connection.id
            session.add_all(
                [
                    Device(user_id=users["alice"].id, agent_id="opc-builder", name="Alice node"),
                    Device(user_id=users["bob"].id, agent_id="shen-zhiye", name="Bob node"),
                    AgentIntroduction(
                        initiator_user_id=users["alice"].id,
                        target_user_id=users["bob"].id,
                        source_agent_id="opc-builder",
                        target_agent_id="shen-zhiye",
                        screening_id="connection-chat-intro",
                        goal="先了解彼此的合作方向",
                        state="CONNECTED",
                        report={
                            "commonGround": ["都在做 Agent 产品"],
                            "complementarity": ["产品与工程互补"],
                            "questions": ["下一步验证什么"],
                        },
                        transcript=[],
                    ),
                ]
            )
            session.commit()

        response = await alice.post(
            f"/api/connections/{connection_id}/chat-rooms",
            json={"goal": "继续讨论两周实验怎么开始", "mode": "HUMAN_DIRECT"},
        )
        assert response.status_code == 201, response.text
        created = response.json()
        room, token = _room_access(created["participantAUrl"])
        view = await alice.get(f"/api/human-agent-chats/{room}", params={"token": token})
        assert view.status_code == 200, view.text
        assert view.json()["viewer"]["agentId"] == "opc-builder"
        assert view.json()["other"]["agentId"] == "shen-zhiye"
        assert "认识阶段互补点：产品与工程互补" in view.json()["context"]["knownFacts"]
        assert view.json()["context"]["openQuestions"] == ["下一步验证什么"]

        forbidden = await alice.post(
            "/api/connections/not-this-connection/chat-rooms",
            json={"goal": "越权", "mode": "HUMAN_DIRECT"},
        )
        assert forbidden.status_code == 404


def select_user(username: str):
    return select(User).where(User.username == username)
