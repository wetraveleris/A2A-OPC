from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
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


def test_connection_uses_one_persistent_room_and_realtime_timeline(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'persistent-chat.db'}"
    app = create_app(
        base_url="http://testserver",
        use_environment_llm=False,
        database_url=database_url,
    )
    with TestClient(app) as alice, TestClient(app) as bob:
        assert alice.post(
            "/api/auth/register",
            json={
                "username": "persist-alice",
                "email": "persist-alice@example.com",
                "password": "alice secure password",
                "displayName": "Alice",
            },
        ).status_code == 201
        assert bob.post(
            "/api/auth/register",
            json={
                "username": "persist-bob",
                "email": "persist-bob@example.com",
                "password": "bob secure password",
                "displayName": "Bob",
            },
        ).status_code == 201
        request = alice.post(
            "/api/connection-requests",
            json={"targetUsername": "persist-bob", "message": "开始聊天"},
        )
        request_id = request.json()["id"]
        assert bob.post(f"/api/connection-requests/{request_id}/accept").status_code == 204

        with app.state.database.session() as session:
            users = {
                username: session.scalar(select_user(username))
                for username in ("persist-alice", "persist-bob")
            }
            connection = session.scalar(
                select(Connection).where(
                    (Connection.user_a_id == users["persist-alice"].id)
                    | (Connection.user_b_id == users["persist-alice"].id)
                )
            )
            session.add_all(
                [
                    Device(
                        user_id=users["persist-alice"].id,
                        agent_id="opc-builder",
                        name="Alice node",
                    ),
                    Device(
                        user_id=users["persist-bob"].id,
                        agent_id="shen-zhiye",
                        name="Bob node",
                    ),
                ]
            )
            session.commit()
            connection_id = connection.id

        created_a_response = alice.post(
            f"/api/connections/{connection_id}/chat-rooms",
            json={"mode": "HUMAN_DIRECT"},
        )
        assert created_a_response.status_code == 201, created_a_response.text
        created_a = created_a_response.json()
        created_b_response = bob.post(
            f"/api/connections/{connection_id}/chat-rooms",
            json={"mode": "HUMAN_DIRECT"},
        )
        assert created_b_response.status_code == 201, created_b_response.text
        created_b = created_b_response.json()
        assert created_a["id"] == created_b["id"]
        assert created_a["participantUrl"] != created_b["participantUrl"]

        room_a, token_a = _room_access(created_a["participantUrl"])
        room_b, token_b = _room_access(created_b["participantUrl"])
        assert room_a == room_b == created_a["id"]
        with alice.websocket_connect(
            f"/api/human-agent-chats/{room_a}/ws?token={token_a}"
        ) as socket_a, bob.websocket_connect(
            f"/api/human-agent-chats/{room_b}/ws?token={token_b}"
        ) as socket_b:
            assert socket_a.receive_json()["messages"] == []
            assert socket_b.receive_json()["messages"] == []
            sent = alice.post(
                f"/api/human-agent-chats/{room_a}/messages",
                params={"token": token_a},
                json={"message": "你是谁？"},
            )
            assert sent.status_code == 200, sent.text
            assert socket_b.receive_json()["messages"][0]["text"] == "你是谁？"
            assert socket_a.receive_json()["messages"][0]["text"] == "你是谁？"

            sent = bob.post(
                f"/api/human-agent-chats/{room_b}/messages",
                params={"token": token_b},
                json={"message": "我是 B 电脑上的本地 Agent。"},
            )
            assert sent.status_code == 200, sent.text
            assert socket_a.receive_json()["messages"][-1]["text"] == "我是 B 电脑上的本地 Agent。"

        reopened = alice.put(
            f"/api/connections/{connection_id}/chat-room",
            json={"mode": "HUMAN_DIRECT"},
        )
        assert reopened.status_code == 200, reopened.text
        assert reopened.json()["id"] == room_a
        reopened_room, reopened_token = _room_access(reopened.json()["participantUrl"])
        history = alice.get(
            f"/api/human-agent-chats/{reopened_room}",
            params={"token": reopened_token},
        )
        assert [message["text"] for message in history.json()["messages"]] == [
            "你是谁？",
            "我是 B 电脑上的本地 Agent。",
        ]
        connection_view = alice.get("/api/connections").json()[0]
        assert connection_view["conversation"]["id"] == room_a
        assert connection_view["conversation"]["messageCount"] == 2
        assert connection_view["conversation"]["lastMessage"] == "我是 B 电脑上的本地 Agent。"

    restarted = create_app(
        base_url="http://testserver",
        use_environment_llm=False,
        database_url=database_url,
    )
    with TestClient(restarted) as client:
        restored = client.get(
            f"/api/human-agent-chats/{room_a}",
            params={"token": token_a},
        )
        assert restored.status_code == 200, restored.text
        assert [message["text"] for message in restored.json()["messages"]] == [
            "你是谁？",
            "我是 B 电脑上的本地 Agent。",
        ]


def select_user(username: str):
    return select(User).where(User.username == username)
