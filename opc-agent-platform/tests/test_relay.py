import asyncio

import pytest

from opc_agent_platform import relay_node
from opc_agent_platform.relay import RelayError, RelayHub
from opc_agent_platform.relay_node import execute_local_task


class FakeWebSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.sent: list[dict[str, object]] = []
        self.closed: tuple[int, str] | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, message: dict[str, object]) -> None:
        self.sent.append(message)

    async def close(self, code: int, reason: str) -> None:
        self.closed = (code, reason)


@pytest.mark.asyncio
async def test_relay_node_bypasses_environment_proxy_for_local_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeCommunicator:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    def cancel_connect(*args: object, **kwargs: object) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(relay_node, "A2ACommunicator", FakeCommunicator)
    monkeypatch.setattr(relay_node, "connect", cancel_connect)

    with pytest.raises(asyncio.CancelledError):
        await relay_node.run_node(
            relay_url="wss://relay.example/api/relay/ws",
            relay_token="secret",
            agent_id="shen-zhiye",
            local_agent_url="http://127.0.0.1:8010/a2a/shen-zhiye",
        )

    assert captured == {
        "base_url": "http://127.0.0.1:8010/a2a/shen-zhiye",
        "trust_env": False,
    }


@pytest.mark.asyncio
async def test_relay_dispatches_to_registered_agent_and_correlates_result() -> None:
    hub = RelayHub(token="relay-secret", request_timeout=1)
    websocket = FakeWebSocket()
    assert hub.authorized("Bearer relay-secret") is True
    assert hub.authorized("Bearer incorrect") is False
    await hub.connect("shen-zhiye", websocket)  # type: ignore[arg-type]
    await hub.receive(
        "shen-zhiye",
        {
            "type": "hello",
            "metadata": {
                "localAgentUrl": "http://127.0.0.1:8010/a2a/shen-zhiye",
                "provider": "ollama",
                "model": "qwen3:1.7b",
            },
        },
    )

    dispatched = asyncio.create_task(
        hub.dispatch("shen-zhiye", {"message": "你是谁"})
    )
    await asyncio.sleep(0)
    task_message = websocket.sent[-1]
    assert task_message["type"] == "task"
    assert task_message["recipientAgentId"] == "shen-zhiye"
    await hub.receive(
        "shen-zhiye",
        {
            "type": "result",
            "requestId": task_message["requestId"],
            "taskId": "local-a2a-task-1",
            "taskState": "TASK_STATE_COMPLETED",
            "response": {"reply": "我是沈知野的 Agent"},
        },
    )

    task_id, task_state, response = await dispatched
    assert task_id == "local-a2a-task-1"
    assert task_state == "TASK_STATE_COMPLETED"
    assert response["reply"] == "我是沈知野的 Agent"
    status = hub.status(["opc-builder", "shen-zhiye"])
    assert status[0]["online"] is False
    assert status[1]["metadata"]["model"] == "qwen3:1.7b"
    assert "localAgentUrl" not in status[1]["metadata"]


@pytest.mark.asyncio
async def test_relay_rejects_dispatch_to_offline_agent() -> None:
    hub = RelayHub()
    with pytest.raises(RelayError, match="offline"):
        await hub.dispatch("opc-builder", {"message": "hello"})


@pytest.mark.asyncio
async def test_agent_node_executes_task_against_local_a2a_runtime() -> None:
    class FakeCommunicator:
        async def send_json_to_url(
            self,
            agent_url: str,
            payload: dict[str, object],
        ) -> tuple[str, str, dict[str, object]]:
            assert agent_url == "http://127.0.0.1:8010/a2a/shen-zhiye"
            assert payload["recipientAgentId"] == "shen-zhiye"
            return (
                "local-task-2",
                "TASK_STATE_COMPLETED",
                {"reply": "B 电脑本地模型已回复"},
            )

    result = await execute_local_task(
        FakeCommunicator(),  # type: ignore[arg-type]
        "http://127.0.0.1:8010/a2a/shen-zhiye",
        {
            "type": "task",
            "requestId": "relay-request-2",
            "payload": {"recipientAgentId": "shen-zhiye"},
        },
    )

    assert result == {
        "type": "result",
        "requestId": "relay-request-2",
        "taskId": "local-task-2",
        "taskState": "TASK_STATE_COMPLETED",
        "response": {"reply": "B 电脑本地模型已回复"},
    }
