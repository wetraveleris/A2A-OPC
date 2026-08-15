from __future__ import annotations

import httpx
import pytest

from opc_agent_platform.app import create_app


@pytest.mark.asyncio
async def test_account_profile_works_and_connections_are_persistent(
    tmp_path,
) -> None:
    app = create_app(
        base_url="http://testserver",
        use_environment_llm=False,
        database_url=f"sqlite:///{tmp_path / 'accounts.db'}",
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as alice, httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as bob:
        alice_register = await alice.post(
            "/api/auth/register",
            json={
                "username": "alice-opc",
                "email": "alice@example.com",
                "password": "correct horse battery staple",
                "displayName": "Alice OPC",
            },
        )
        assert alice_register.status_code == 201, alice_register.text
        assert alice_register.json()["user"]["username"] == "alice-opc"

        profile = await alice.put(
            "/api/me/profile",
            json={
                "role": "独立产品开发者",
                "projectSummary": "为小团队搭建可验证的 Agent 工具。",
                "offers": ["产品原型", "用户研究"],
                "needs": ["工程协作"],
            },
        )
        assert profile.status_code == 200, profile.text
        assert profile.json()["projectSummary"] == "为小团队搭建可验证的 Agent 工具。"

        work = await alice.post(
            "/api/me/works",
            json={
                "title": "Agent 协作原型",
                "summary": "一个两周小实验。",
                "status": "IN_PROGRESS",
                "visibility": "PUBLIC",
                "skills": ["product", "research"],
            },
        )
        assert work.status_code == 201, work.text
        work_id = work.json()["id"]
        assert (await alice.get("/api/me/works")).json()[0]["id"] == work_id

        discovery = await bob.get("/api/discovery/feed")
        assert discovery.status_code == 200, discovery.text
        assert discovery.json()[0]["username"] == "alice-opc"
        assert discovery.json()[0]["works"][0]["title"] == "Agent 协作原型"
        assert "email" not in discovery.json()[0]
        assert "avatarUrl" not in discovery.json()[0]
        assert "introVideoUrl" not in discovery.json()[0]
        assert "coverUrl" not in discovery.json()[0]["works"][0]
        assert "videoUrl" not in discovery.json()[0]["works"][0]

        media_route = await bob.get("/视频/121311_5b97ff14ca15b2db.mp4")
        assert media_route.status_code == 404

        assert (await alice.post("/api/schedule-inquiries", json={})).status_code == 404

        bob_register = await bob.post(
            "/api/auth/register",
            json={
                "username": "bob-opc",
                "email": "bob@example.com",
                "password": "another correct battery phrase",
                "displayName": "Bob OPC",
            },
        )
        assert bob_register.status_code == 201, bob_register.text
        bob_profile = await bob.put(
            "/api/me/profile",
            json={
                "role": "Agent 工程师",
                "offers": ["工程协作"],
                "needs": ["产品原型"],
            },
        )
        assert bob_profile.status_code == 200, bob_profile.text

        bob_feed = await bob.get("/api/discovery/feed")
        alice_discovery = next(
            item for item in bob_feed.json() if item["username"] == "alice-opc"
        )
        assert alice_discovery["relationState"] == "NONE"
        assessment = await bob.post(
            f"/api/discovery/{alice_discovery['profileId']}/assessment"
        )
        assert assessment.status_code == 200, assessment.text
        assert assessment.json()["basis"] == "PUBLIC_PROFILE"
        assert assessment.json()["canRequest"] is True
        assert assessment.json()["complementarity"]

        request = await bob.post(
            "/api/connection-requests",
            json={
                "targetUsername": "alice-opc",
                "message": "一起验证 Agent 协作。",
            },
        )
        assert request.status_code == 201, request.text
        request_id = request.json()["id"]
        bob_feed_after_request = await bob.get("/api/discovery/feed")
        alice_after_request = next(
            item
            for item in bob_feed_after_request.json()
            if item["username"] == "alice-opc"
        )
        assert alice_after_request["relationState"] == "PENDING_OUTGOING"

        incoming = await alice.get("/api/connection-requests")
        assert incoming.status_code == 200
        assert incoming.json()[0]["direction"] == "INCOMING"
        assert "email" not in incoming.json()[0]["user"]

        accepted = await alice.post(f"/api/connection-requests/{request_id}/accept")
        assert accepted.status_code == 204, accepted.text
        connections = await bob.get("/api/connections")
        assert connections.status_code == 200
        assert connections.json()[0]["user"]["username"] == "alice-opc"

        await alice.post("/api/auth/logout")
        unauthenticated = await alice.get("/api/auth/me")
        assert unauthenticated.status_code == 401
        logged_in = await alice.post(
            "/api/auth/login",
            json={
                "identity": "alice-opc",
                "password": "correct horse battery staple",
            },
        )
        assert logged_in.status_code == 200, logged_in.text
        assert (await alice.get("/api/auth/me")).status_code == 200

        deleted = await alice.delete(f"/api/me/works/{work_id}")
        assert deleted.status_code == 204
        assert (await alice.get("/api/me/works")).json() == []
