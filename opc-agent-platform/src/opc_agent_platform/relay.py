from __future__ import annotations

import asyncio
import secrets

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket


class RelayError(RuntimeError):
    pass


@dataclass
class RelayConnection:
    websocket: WebSocket
    connected_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    last_seen: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    metadata: dict[str, Any] = field(default_factory=dict)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class RelayHub:
    def __init__(self, token: str = "", request_timeout: float = 150.0) -> None:
        self.token = token
        self.request_timeout = request_timeout
        self._connections: dict[str, RelayConnection] = {}
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    def authorized(self, authorization: str | None) -> bool:
        if not self.token:
            return True
        if not authorization or not authorization.startswith("Bearer "):
            return False
        return secrets.compare_digest(authorization[7:], self.token)

    async def connect(self, agent_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        connection = RelayConnection(websocket=websocket)
        async with self._lock:
            previous = self._connections.get(agent_id)
            self._connections[agent_id] = connection
        if previous:
            try:
                await previous.websocket.close(code=4001, reason="Agent reconnected")
            except RuntimeError:
                pass
        await websocket.send_json(
            {"type": "registered", "agentId": agent_id}
        )

    async def disconnect(self, agent_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            current = self._connections.get(agent_id)
            if current and current.websocket is websocket:
                del self._connections[agent_id]

    async def receive(self, agent_id: str, message: dict[str, Any]) -> None:
        connection = self._connections.get(agent_id)
        if connection:
            connection.last_seen = datetime.now(timezone.utc)

        message_type = str(message.get("type", ""))
        if message_type == "hello":
            if connection:
                metadata = message.get("metadata", {})
                connection.metadata = (
                    {
                        key: str(metadata[key])
                        for key in ("provider", "model")
                        if metadata.get(key)
                    }
                    if isinstance(metadata, dict)
                    else {}
                )
            return
        if message_type == "heartbeat":
            return
        if message_type != "result":
            raise RelayError(f"Unsupported relay message type: {message_type}")

        request_id = str(message.get("requestId", ""))
        future = self._pending.get(request_id)
        if future and not future.done():
            future.set_result(message)

    async def dispatch(
        self,
        agent_id: str,
        payload: dict[str, Any],
    ) -> tuple[str, str, dict[str, Any]]:
        connection = self._connections.get(agent_id)
        if connection is None:
            raise RelayError(f"Agent {agent_id} is offline")

        request_id = secrets.token_urlsafe(18)
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[request_id] = future
        try:
            async with connection.send_lock:
                await connection.websocket.send_json(
                    {
                        "type": "task",
                        "requestId": request_id,
                        "recipientAgentId": agent_id,
                        "payload": payload,
                    }
                )
            try:
                result = await asyncio.wait_for(
                    future,
                    timeout=self.request_timeout,
                )
            except TimeoutError as exc:
                raise RelayError(
                    f"Agent {agent_id} did not respond before timeout"
                ) from exc
        finally:
            self._pending.pop(request_id, None)

        error = str(result.get("error", "")).strip()
        if error:
            raise RelayError(f"Agent {agent_id} failed: {error}")
        response = result.get("response")
        if not isinstance(response, dict):
            raise RelayError(f"Agent {agent_id} returned an invalid response")
        return (
            str(result.get("taskId", request_id)),
            str(result.get("taskState", "TASK_STATE_COMPLETED")),
            response,
        )

    def is_online(self, agent_id: str) -> bool:
        return agent_id in self._connections

    def status(self, known_agent_ids: list[str]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for agent_id in known_agent_ids:
            connection = self._connections.get(agent_id)
            result.append(
                {
                    "agentId": agent_id,
                    "online": connection is not None,
                    "connectedAt": (
                        connection.connected_at.isoformat() if connection else None
                    ),
                    "lastSeen": (
                        connection.last_seen.isoformat() if connection else None
                    ),
                    "metadata": connection.metadata if connection else {},
                }
            )
        return result
