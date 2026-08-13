import asyncio
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from opc_agent_platform.app import create_app


def _access(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    return query["room"][0], query["token"][0]


@pytest.mark.asyncio
async def test_public_computer_topology_supports_all_control_modes_and_routes_to_b(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote_b_url = "https://public.example/agent-b/a2a/shen-zhiye"
    monkeypatch.setenv("OPC_REMOTE_AGENT_B_URL", remote_b_url)
    app = create_app(base_url="https://public.example", use_environment_llm=False)
    dispatched: list[tuple[str, dict[str, object]]] = []

    async def fake_send(
        agent_url: str,
        payload: dict[str, object],
    ) -> tuple[str, str, dict[str, object]]:
        dispatched.append((agent_url, payload))
        return (
            "task-from-computer-b",
            "TASK_STATE_COMPLETED",
            {"reply": "我是电脑 B 上的沈知野 Agent。", "contextPatch": {}},
        )

    app.state.human_chat_service.communicator.send_json_to_url = fake_send
    transport = httpx.ASGITransport(app=app)
    created_rooms: dict[str, dict[str, object]] = {}

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        for mode in ("HUMAN_APPROVAL", "AGENT_TAKEOVER", "HUMAN_DIRECT"):
            response = await client.post(
                "/api/human-agent-chats",
                json={
                    "goal": f"验证 {mode} 的公网双电脑 Agent 沟通",
                    "mode": mode,
                    "topology": "PUBLIC_A_B",
                },
            )
            assert response.status_code == 201, response.text
            created_rooms[mode] = response.json()
            assert response.json()["topology"] == "PUBLIC_A_B"
            assert response.json()["agentAUrl"] == (
                "https://public.example/a2a/opc-builder"
            )
            assert response.json()["agentBUrl"] == remote_b_url

        approval = created_rooms["HUMAN_APPROVAL"]
        room, token_a = _access(str(approval["participantAUrl"]))
        view = (
            await client.get(
                f"/api/human-agent-chats/{room}", params={"token": token_a}
            )
        ).json()
        assert view["viewer"]["computerName"] == "电脑 A"
        assert view["other"]["computerName"] == "电脑 B"
        approved = await client.post(
            f"/api/human-agent-chats/{room}/approve",
            params={"token": token_a},
            json={
                "message": "你是谁？",
                "expectedVersion": view["version"],
            },
        )

    assert approved.status_code == 200, approved.text
    assert dispatched[0][0] == remote_b_url
    assert dispatched[0][1]["recipientAgentId"] == "shen-zhiye"
    assert approved.json()["a2ATurns"] == []


@pytest.mark.asyncio
async def test_two_users_approve_private_agent_drafts_turn_by_turn() -> None:
    app = create_app(base_url="http://testserver", use_environment_llm=False)
    transport = httpx.ASGITransport(app=app)
    app.state.human_chat_service.communicator.transport = transport

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        created_response = await client.post(
            "/api/human-agent-chats",
            json={
                "goal": "验证两个用户能否分别介入 Agent 对话",
                "maxTurns": 2,
            },
        )
        assert created_response.status_code == 201, created_response.text
        created = created_response.json()
        room_a, token_a = _access(created["participantAUrl"])
        room_b, token_b = _access(created["participantBUrl"])
        assert room_a == room_b == created["id"]
        assert token_a != token_b

        view_a = (
            await client.get(
                f"/api/human-agent-chats/{room_a}", params={"token": token_a}
            )
        ).json()
        view_b = (
            await client.get(
                f"/api/human-agent-chats/{room_b}", params={"token": token_b}
            )
        ).json()
        assert view_a["canAct"] is True
        assert view_a["pendingDraft"]["originalText"].startswith("验证两个用户")
        assert view_b["canAct"] is False
        assert view_b["pendingDraft"] is None

        after_a_response = await client.post(
            f"/api/human-agent-chats/{room_a}/approve",
            params={"token": token_a},
            json={
                "message": "请先提出一个低风险的两周实验。",
                "expectedVersion": view_a["version"],
            },
        )
        assert after_a_response.status_code == 200, after_a_response.text
        after_a = after_a_response.json()
        assert after_a["canAct"] is False
        assert after_a["state"] == "WAITING_OWNER_B"
        assert after_a["pendingDraft"] is None
        assert len(after_a["messages"]) == 1
        assert after_a["a2ATurns"] == []

        waiting_b = (
            await client.get(
                f"/api/human-agent-chats/{room_b}", params={"token": token_b}
            )
        ).json()
        assert waiting_b["canAct"] is True
        assert waiting_b["pendingDraft"]["turn"] == 1
        assert waiting_b["pendingDraft"]["sourceTaskId"]
        original_b = waiting_b["pendingDraft"]["originalText"]
        edited_b = original_b + " 我本人补充：验收时必须使用真实用户。"

        after_b_response = await client.post(
            f"/api/human-agent-chats/{room_b}/approve",
            params={"token": token_b},
            json={
                "message": edited_b,
                "expectedVersion": waiting_b["version"],
            },
        )
        assert after_b_response.status_code == 200, after_b_response.text

        waiting_a = (
            await client.get(
                f"/api/human-agent-chats/{room_a}", params={"token": token_a}
            )
        ).json()
        assert waiting_a["canAct"] is True
        assert waiting_a["pendingDraft"]["turn"] == 2
        assert waiting_a["messages"][1]["text"] == edited_b
        assert waiting_a["messages"][1]["humanEdited"] is True
        assert len(waiting_a["a2ATurns"]) == 1
        assert waiting_a["a2ATurns"][0]["turn"] == 1

        completed_response = await client.post(
            f"/api/human-agent-chats/{room_a}/approve",
            params={"token": token_a},
            json={
                "message": waiting_a["pendingDraft"]["originalText"],
                "expectedVersion": waiting_a["version"],
            },
        )

    assert completed_response.status_code == 200, completed_response.text
    completed = completed_response.json()
    assert completed["state"] == "COMPLETED"
    assert completed["canAct"] is False
    assert completed["pendingDraft"] is None
    assert len(completed["messages"]) == 3
    assert len(completed["a2ATurns"]) == 2


@pytest.mark.asyncio
async def test_human_chat_enforces_token_owner_and_version() -> None:
    app = create_app(base_url="http://testserver", use_environment_llm=False)
    transport = httpx.ASGITransport(app=app)
    app.state.human_chat_service.communicator.transport = transport

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        created = (
            await client.post(
                "/api/human-agent-chats",
                json={"goal": "验证参与方权限", "maxTurns": 1},
            )
        ).json()
        room, token_a = _access(created["participantAUrl"])
        _, token_b = _access(created["participantBUrl"])

        invalid = await client.get(
            f"/api/human-agent-chats/{room}", params={"token": "invalid"}
        )
        other_owner = await client.post(
            f"/api/human-agent-chats/{room}/approve",
            params={"token": token_b},
            json={"message": "越权发送", "expectedVersion": 1},
        )
        approved = await client.post(
            f"/api/human-agent-chats/{room}/approve",
            params={"token": token_a},
            json={"message": "正常发送", "expectedVersion": 1},
        )
        stale = await client.post(
            f"/api/human-agent-chats/{room}/approve",
            params={"token": token_b},
            json={"message": "使用过期版本", "expectedVersion": 1},
        )

    assert invalid.status_code == 403
    assert other_owner.status_code == 403
    assert approved.status_code == 200
    assert stale.status_code == 409


@pytest.mark.asyncio
async def test_current_draft_owner_can_reject_conversation() -> None:
    app = create_app(base_url="http://testserver", use_environment_llm=False)
    transport = httpx.ASGITransport(app=app)
    app.state.human_chat_service.communicator.transport = transport

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        created = (
            await client.post(
                "/api/human-agent-chats",
                json={"goal": "验证拒绝流程", "maxTurns": 2},
            )
        ).json()
        room, token_a = _access(created["participantAUrl"])
        rejected = await client.post(
            f"/api/human-agent-chats/{room}/reject",
            params={"token": token_a},
            json={"reason": "需求边界不清晰", "expectedVersion": 1},
        )

    assert rejected.status_code == 200
    assert rejected.json()["state"] == "REJECTED"
    assert rejected.json()["audit"][-1]["detail"] == "需求边界不清晰"


@pytest.mark.asyncio
async def test_human_chat_sse_serializes_initial_view() -> None:
    app = create_app(base_url="http://testserver", use_environment_llm=False)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        created = (
            await client.post(
                "/api/human-agent-chats",
                json={"goal": "验证 SSE 实时视图", "maxTurns": 1},
            )
        ).json()
        room, token_a = _access(created["participantAUrl"])
        view = app.state.human_chat_service.view(room, token_a)
        first_chunk = app.state.human_chat_service._sse(
            "conversation.updated",
            view,
        )

    assert "event: conversation.updated" in first_chunk
    assert '"state":"WAITING_OWNER_A"' in first_chunk
    assert "createdAt" in first_chunk


@pytest.mark.asyncio
async def test_agent_takeover_runs_a2a_turns_without_human_approval() -> None:
    app = create_app(base_url="http://testserver", use_environment_llm=False)
    transport = httpx.ASGITransport(app=app)
    app.state.human_chat_service.communicator.transport = transport

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        created_response = await client.post(
            "/api/human-agent-chats",
            json={
                "goal": "由两个 Agent 自动形成一个可验证的合作方案",
                "maxTurns": 2,
                "mode": "AGENT_TAKEOVER",
            },
        )
        assert created_response.status_code == 201, created_response.text
        created = created_response.json()
        room, token_a = _access(created["participantAUrl"])
        assert created["mode"] == "AGENT_TAKEOVER"
        assert created["state"] == "AGENT_READY"

        ready = (
            await client.get(
                f"/api/human-agent-chats/{room}",
                params={"token": token_a},
            )
        ).json()
        assert ready["state"] == "AGENT_READY"
        assert ready["canStart"] is True
        assert ready["canStop"] is False
        assert ready["messages"] == []
        assert ready["a2ATurns"] == []

        started = await client.post(
            f"/api/human-agent-chats/{room}/start",
            params={"token": token_a},
            json={"reason": "用户 A 确认托管"},
        )
        assert started.status_code == 200
        assert started.json()["state"] == "AGENT_RUNNING"

        for _ in range(100):
            response = await client.get(
                f"/api/human-agent-chats/{room}",
                params={"token": token_a},
            )
            view = response.json()
            if view["state"] in {"COMPLETED", "FAILED"}:
                break
            await asyncio.sleep(0.03)

    assert view["state"] == "COMPLETED", view.get("error")
    assert view["mode"] == "AGENT_TAKEOVER"
    assert view["canAct"] is False
    assert view["canStart"] is False
    assert view["canStop"] is False
    assert len(view["messages"]) == 3
    assert len(view["a2ATurns"]) == 2
    assert all(message["humanApproved"] is False for message in view["messages"])
    actions = [event["action"] for event in view["audit"]]
    assert actions.count("agent.message.sent") == 3
    assert "conversation.completed" in actions


@pytest.mark.asyncio
async def test_manual_approval_session_cannot_use_takeover_stop() -> None:
    app = create_app(base_url="http://testserver", use_environment_llm=False)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        created = (
            await client.post(
                "/api/human-agent-chats",
                json={"goal": "人工审核模式不能调用自动接管停止接口"},
            )
        ).json()
        room, token_a = _access(created["participantAUrl"])
        stopped = await client.post(
            f"/api/human-agent-chats/{room}/stop",
            params={"token": token_a},
            json={"reason": "测试停止"},
        )
        started = await client.post(
            f"/api/human-agent-chats/{room}/start",
            params={"token": token_a},
            json={"reason": "测试启动"},
        )

    assert stopped.status_code == 400
    assert started.status_code == 400


@pytest.mark.asyncio
async def test_takeover_stop_waits_for_inflight_task_without_sending_reply() -> None:
    app = create_app(base_url="http://testserver", use_environment_llm=False)
    transport = httpx.ASGITransport(app=app)
    communicator = app.state.human_chat_service.communicator
    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed_reply(
        _agent_url: str,
        _payload: dict[str, object],
    ) -> tuple[str, str, dict[str, object]]:
        started.set()
        await release.wait()
        return (
            "task-inflight",
            "TASK_STATE_COMPLETED",
            {
                "reply": "这条回复在停止后不应继续自动发送。",
                "contextPatch": {},
            },
        )

    communicator.send_json_to_url = delayed_reply

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        created = (
            await client.post(
                "/api/human-agent-chats",
                json={
                    "goal": "在模型生成期间停止 Agent 接管",
                    "maxTurns": 2,
                    "mode": "AGENT_TAKEOVER",
                },
            )
        ).json()
        room, token_b = _access(created["participantBUrl"])
        start_response = await client.post(
            f"/api/human-agent-chats/{room}/start",
            params={"token": token_b},
            json={"reason": "用户 B 确认托管"},
        )
        assert start_response.status_code == 200
        await asyncio.wait_for(started.wait(), timeout=1)

        stopping_response = await client.post(
            f"/api/human-agent-chats/{room}/stop",
            params={"token": token_b},
            json={"reason": "用户 B 主动介入"},
        )
        assert stopping_response.status_code == 200
        assert stopping_response.json()["state"] == "STOPPING"

        release.set()
        for _ in range(100):
            stopped = (
                await client.get(
                    f"/api/human-agent-chats/{room}",
                    params={"token": token_b},
                )
            ).json()
            if stopped["state"] == "STOPPED":
                break
            await asyncio.sleep(0.02)

    assert stopped["state"] == "STOPPED"
    assert len(stopped["messages"]) == 1
    assert len(stopped["a2ATurns"]) == 1
    assert stopped["a2ATurns"][0]["taskId"] == "task-inflight"
    assert stopped["audit"][-1]["action"] == "takeover.stopped"


@pytest.mark.asyncio
async def test_continuous_takeover_stops_only_when_user_requests_it() -> None:
    app = create_app(base_url="http://testserver", use_environment_llm=False)
    transport = httpx.ASGITransport(app=app)
    communicator = app.state.human_chat_service.communicator
    calls = 0
    second_call_started = asyncio.Event()
    release_second_call = asyncio.Event()

    async def controlled_reply(
        _agent_url: str,
        payload: dict[str, object],
    ) -> tuple[str, str, dict[str, object]]:
        nonlocal calls
        calls += 1
        if calls == 2:
            second_call_started.set()
            await release_second_call.wait()
        return (
            f"task-continuous-{calls}",
            "TASK_STATE_COMPLETED",
            {"reply": f"持续回复 {calls}", "contextPatch": {}},
        )

    communicator.send_json_to_url = controlled_reply

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        created = (
            await client.post(
                "/api/human-agent-chats",
                json={"goal": "持续沟通直到用户停止", "mode": "AGENT_TAKEOVER"},
            )
        ).json()
        room, token_a = _access(created["participantAUrl"])
        ready = (
            await client.get(
                f"/api/human-agent-chats/{room}", params={"token": token_a}
            )
        ).json()
        assert ready["runPolicy"] == "CONTINUOUS"
        assert ready["maxTurns"] is None

        await client.post(
            f"/api/human-agent-chats/{room}/start",
            params={"token": token_a},
            json={"reason": "开始持续托管"},
        )
        await asyncio.wait_for(second_call_started.wait(), timeout=1)
        stopping = await client.post(
            f"/api/human-agent-chats/{room}/stop",
            params={"token": token_a},
            json={"reason": "用户主动停止持续托管"},
        )
        assert stopping.json()["state"] == "STOPPING"
        release_second_call.set()

        for _ in range(100):
            stopped = (
                await client.get(
                    f"/api/human-agent-chats/{room}", params={"token": token_a}
                )
            ).json()
            if stopped["state"] == "STOPPED":
                break
            await asyncio.sleep(0.02)

    assert stopped["state"] == "STOPPED"
    assert stopped["canStart"] is True
    assert len(stopped["messages"]) == 2
    assert len(stopped["a2ATurns"]) == 2
    assert "conversation.completed" not in {
        event["action"] for event in stopped["audit"]
    }

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        direct = await client.post(
            f"/api/human-agent-chats/{room}/mode",
            params={"token": token_a},
            json={"mode": "HUMAN_DIRECT", "reason": "停止后转人工"},
        )
        assert direct.json()["state"] == "HUMAN_DIRECT"
        sent = await client.post(
            f"/api/human-agent-chats/{room}/messages",
            params={"token": token_a},
            json={"message": "人工接续消息"},
        )
    assert sent.json()["messages"][-1]["turn"] == 3


@pytest.mark.asyncio
async def test_continuous_chat_pauses_silently_when_agent_has_nothing_to_add() -> None:
    app = create_app(base_url="http://testserver", use_environment_llm=False)
    transport = httpx.ASGITransport(app=app)

    async def stop_reply(
        _agent_url: str,
        payload: dict[str, object],
    ) -> tuple[str, str, dict[str, object]]:
        assert payload["conversationTopic"] == "两个 Agent 自然聊天"
        assert payload["recentHistory"]
        return (
            "task-chat-stop",
            "TASK_STATE_COMPLETED",
            {"action": "STOP", "reply": "", "contextPatch": {}},
        )

    app.state.human_chat_service.communicator.send_json_to_url = stop_reply

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        created = (
            await client.post(
                "/api/human-agent-chats",
                json={"goal": "两个 Agent 自然聊天", "mode": "AGENT_TAKEOVER"},
            )
        ).json()
        room, token_a = _access(created["participantAUrl"])
        await client.post(
            f"/api/human-agent-chats/{room}/start",
            params={"token": token_a},
            json={"reason": "开始聊天"},
        )
        for _ in range(100):
            view = (
                await client.get(
                    f"/api/human-agent-chats/{room}", params={"token": token_a}
                )
            ).json()
            if view["state"] == "STOPPED":
                break
            await asyncio.sleep(0.02)

    assert view["state"] == "STOPPED"
    assert view["canStart"] is True
    assert len(view["messages"]) == 1
    assert len(view["a2ATurns"]) == 1
    assert "没有新的有用内容" in view["pauseReason"]
    assert view["audit"][-1]["action"] == "takeover.paused"


@pytest.mark.asyncio
async def test_human_message_joins_history_without_resetting_conversation() -> None:
    app = create_app(base_url="http://testserver", use_environment_llm=False)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        created = (
            await client.post(
                "/api/human-agent-chats",
                json={"goal": "讨论产品合作", "mode": "HUMAN_DIRECT"},
            )
        ).json()
        room, token_a = _access(created["participantAUrl"])
        updated = (
            await client.post(
                f"/api/human-agent-chats/{room}/messages",
                params={"token": token_a},
                json={"message": "我本人介入：先讨论用户最痛的场景。"},
            )
        ).json()
        ready = (
            await client.post(
                f"/api/human-agent-chats/{room}/mode",
                params={"token": token_a},
                json={"mode": "AGENT_TAKEOVER", "reason": "交回 Agent"},
            )
        ).json()

    assert updated["goal"] == "讨论产品合作"
    assert updated["context"]["goal"] == "讨论产品合作"
    assert updated["messages"][-1]["source"] == "HUMAN_DIRECT"
    assert updated["audit"][-1]["action"] == "human.message.sent"
    assert ready["state"] == "AGENT_READY"
    assert ready["canStart"] is True


@pytest.mark.asyncio
async def test_switches_between_takeover_approval_and_human_direct() -> None:
    app = create_app(base_url="http://testserver", use_environment_llm=False)
    transport = httpx.ASGITransport(app=app)
    app.state.human_chat_service.communicator.transport = transport

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        created = (
            await client.post(
                "/api/human-agent-chats",
                json={
                    "goal": "在三种控制模式之间切换",
                    "mode": "AGENT_TAKEOVER",
                    "maxTurns": 3,
                },
            )
        ).json()
        room, token_a = _access(created["participantAUrl"])
        _, token_b = _access(created["participantBUrl"])

        approval = await client.post(
            f"/api/human-agent-chats/{room}/mode",
            params={"token": token_a},
            json={"mode": "HUMAN_APPROVAL", "reason": "先逐条审核"},
        )
        assert approval.status_code == 200
        approval_view = approval.json()
        assert approval_view["mode"] == "HUMAN_APPROVAL"
        assert approval_view["state"] == "WAITING_OWNER_A"
        assert approval_view["canAct"] is True

        direct = await client.post(
            f"/api/human-agent-chats/{room}/mode",
            params={"token": token_b},
            json={"mode": "HUMAN_DIRECT", "reason": "改为本人沟通"},
        )
        assert direct.json()["state"] == "HUMAN_DIRECT"
        assert direct.json()["canSendDirect"] is True

        sent = await client.post(
            f"/api/human-agent-chats/{room}/messages",
            params={"token": token_b},
            json={"message": "这是用户 B 直接发送的补充信息。"},
        )
        sent_view = sent.json()
        assert sent_view["messages"][-1]["source"] == "HUMAN_DIRECT"
        assert sent_view["messages"][-1]["speakerAgentId"] == "shen-zhiye"

        takeover = await client.post(
            f"/api/human-agent-chats/{room}/mode",
            params={"token": token_a},
            json={"mode": "AGENT_TAKEOVER", "reason": "把最新上下文交回 Agent"},
        )
        takeover_view = takeover.json()
        assert takeover_view["mode"] == "AGENT_TAKEOVER"
        assert takeover_view["state"] == "AGENT_READY"
        assert takeover_view["canStart"] is True

        started = await client.post(
            f"/api/human-agent-chats/{room}/start",
            params={"token": token_a},
            json={"reason": "继续托管"},
        )
        assert started.json()["state"] == "AGENT_RUNNING"
        for _ in range(100):
            final = (
                await client.get(
                    f"/api/human-agent-chats/{room}", params={"token": token_a}
                )
            ).json()
            if final["state"] in {"COMPLETED", "FAILED"}:
                break
            await asyncio.sleep(0.03)

    assert final["state"] == "COMPLETED", final.get("error")
    direct_messages = [
        message for message in final["messages"] if message["source"] == "HUMAN_DIRECT"
    ]
    assert len(direct_messages) == 1
    assert final["a2ATurns"][0]["request"]["message"].startswith("这是用户 B")
