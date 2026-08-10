from __future__ import annotations

import asyncio
import json

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .calendar_tool import check_availability
from .conversation import A2ACommunicator, A2AProtocolError
from .models import (
    CreateLiveScheduleRequest,
    LiveConversationMessage,
    LiveConversationRecord,
    LiveConversationState,
)
from .profiles import get_profile


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def sse(event: str, payload: dict[str, Any]) -> str:
    """Encode one browser event without buffering multiple logical messages."""
    return f"event: {event}\ndata: {_json(payload)}\n\n"


class LiveConversationStore:
    def __init__(self) -> None:
        self._records: dict[str, LiveConversationRecord] = {}
        self._lock = asyncio.Lock()

    async def create(self, request: CreateLiveScheduleRequest) -> LiveConversationRecord:
        record = LiveConversationRecord(
            id=str(uuid4()),
            from_agent_id=request.from_agent_id,
            to_agent_id=request.to_agent_id,
            requested_start=request.requested_start,
            duration_minutes=request.duration_minutes,
            topic=request.topic,
            state=LiveConversationState.CREATED,
        )
        async with self._lock:
            self._records[record.id] = record
        return record.model_copy(deep=True)

    async def get(self, conversation_id: str) -> LiveConversationRecord:
        async with self._lock:
            record = self._records.get(conversation_id)
            if record is None:
                raise KeyError(f"Unknown live conversation: {conversation_id}")
            return record.model_copy(deep=True)

    async def set_state(
        self,
        conversation_id: str,
        state: LiveConversationState,
        *,
        agents_confirmed: bool | None = None,
        error: str | None = None,
    ) -> LiveConversationRecord:
        async with self._lock:
            record = self._records[conversation_id]
            record.state = state
            if agents_confirmed is not None:
                record.agents_confirmed = agents_confirmed
            if error is not None:
                record.error = error
            record.updated_at = datetime.now(timezone.utc)
            return record.model_copy(deep=True)

    async def add_message(
        self,
        conversation_id: str,
        message: LiveConversationMessage,
    ) -> LiveConversationRecord:
        async with self._lock:
            record = self._records[conversation_id]
            record.messages.append(message)
            record.updated_at = datetime.now(timezone.utc)
            return record.model_copy(deep=True)

    async def confirm(
        self,
        conversation_id: str,
        agent_id: str,
        decision: str,
    ) -> LiveConversationRecord:
        async with self._lock:
            record = self._records.get(conversation_id)
            if record is None:
                raise KeyError(f"Unknown live conversation: {conversation_id}")
            if agent_id not in {record.from_agent_id, record.to_agent_id}:
                raise ValueError("Only a live conversation participant can confirm")
            if record.state not in {
                LiveConversationState.WAITING_HUMAN_CONFIRMATION,
                LiveConversationState.CONFIRMED,
            }:
                raise ValueError("Live conversation is not waiting for confirmation")
            if decision == "decline":
                record.state = LiveConversationState.DECLINED
            elif agent_id not in record.confirmations:
                record.confirmations.append(agent_id)
                if len(record.confirmations) == 2:
                    record.state = LiveConversationState.CONFIRMED
            record.updated_at = datetime.now(timezone.utc)
            return record.model_copy(deep=True)


class LiveConversationService:
    """Orchestrates a real A2A stream as an alternating Agent conversation."""

    def __init__(
        self,
        store: LiveConversationStore,
        communicator: A2ACommunicator,
    ) -> None:
        self.store = store
        self.communicator = communicator

    async def create(self, request: CreateLiveScheduleRequest) -> LiveConversationRecord:
        source = get_profile(request.from_agent_id)
        target = get_profile(request.to_agent_id)
        if source.id == target.id:
            raise ValueError("An Agent cannot talk to itself")
        return await self.store.create(request)

    async def stream(self, conversation_id: str) -> AsyncIterator[str]:
        record = await self.store.get(conversation_id)
        source = get_profile(record.from_agent_id)
        target = get_profile(record.to_agent_id)
        await self.store.set_state(conversation_id, LiveConversationState.STREAMING)
        yield sse(
            "conversation.started",
            {
                "conversationId": record.id,
                "state": LiveConversationState.STREAMING.value,
                "fromAgentId": source.id,
                "toAgentId": target.id,
                "topic": record.topic,
                "requestedStart": record.requested_start.isoformat(),
                "durationMinutes": record.duration_minutes,
            },
        )

        previous_message: str | None = None
        source_availability = check_availability(
            source.id, record.requested_start, record.duration_minutes
        )
        target_availability = check_availability(
            target.id, record.requested_start, record.duration_minutes
        )
        mutually_available = (
            source_availability.available and target_availability.available
        )
        participants = (
            (source, target, source, target)
            if mutually_available
            else ((source, target) if not target_availability.available else (source, target, source))
        )
        try:
            for turn, speaker in enumerate(participants, start=1):
                other = target if speaker.id == source.id else source
                speaker_availability = check_availability(
                    speaker.id, record.requested_start, record.duration_minutes
                )
                other_availability = check_availability(
                    other.id, record.requested_start, record.duration_minutes
                )
                payload = {
                    "protocol": "opc.live_schedule.v1",
                    "conversationId": record.id,
                    "turn": turn,
                    "speakerAgentId": speaker.id,
                    "otherAgentId": other.id,
                    "requestedStart": record.requested_start.isoformat(),
                    "durationMinutes": record.duration_minutes,
                    "topic": record.topic,
                    "availabilityStatus": speaker_availability.status.value,
                    "otherAvailabilityStatus": other_availability.status.value,
                    "previousMessage": previous_message,
                }
                yield sse(
                    "message.started",
                    {
                        "conversationId": record.id,
                        "turn": turn,
                        "speakerAgentId": speaker.id,
                        "recipientAgentId": other.id,
                        "speakerName": speaker.name,
                        "recipientName": other.name,
                    },
                )
                chunks: list[str] = []
                task_id = ""
                async for event in self.communicator.stream_text(speaker.id, payload):
                    task_id = event.task_id or task_id
                    if event.kind != "delta" or not event.text:
                        continue
                    chunks.append(event.text)
                    yield sse(
                        "message.delta",
                        {
                            "conversationId": record.id,
                            "turn": turn,
                            "speakerAgentId": speaker.id,
                            "recipientAgentId": other.id,
                            "text": event.text,
                        },
                    )
                text = "".join(chunks).strip()
                if not text:
                    raise A2AProtocolError("Streaming Agent returned an empty message")
                message = LiveConversationMessage(
                    turn=turn,
                    speaker_agent_id=speaker.id,
                    recipient_agent_id=other.id,
                    text=text,
                    task_id=task_id,
                    task_state="TASK_STATE_COMPLETED",
                )
                await self.store.add_message(conversation_id, message)
                previous_message = text
                yield sse(
                    "message.completed",
                    {
                        **message.model_dump(mode="json", by_alias=True),
                        "speakerName": speaker.name,
                        "recipientName": other.name,
                    },
                )

            if mutually_available:
                await self.store.set_state(
                    conversation_id,
                    LiveConversationState.WAITING_HUMAN_CONFIRMATION,
                    agents_confirmed=True,
                )
                completion_message = "双方 Agent 已确认这个时间都有空，等待两位本人确认。"
            else:
                await self.store.set_state(
                    conversation_id,
                    LiveConversationState.NO_COMMON_SLOT,
                    agents_confirmed=False,
                )
                completion_message = "这个时间有冲突，Agent 未生成暂定会面。"
            final = await self.store.get(conversation_id)
            yield sse(
                "conversation.completed",
                {
                    **final.model_dump(mode="json", by_alias=True),
                    "message": completion_message,
                },
            )
        except Exception as exc:
            failed = await self.store.set_state(
                conversation_id,
                LiveConversationState.FAILED,
                error=str(exc),
            )
            yield sse(
                "error",
                {
                    "conversationId": conversation_id,
                    "message": "Agent 沟通中断，请稍后重试。",
                    "detail": str(exc),
                    "state": failed.state.value,
                },
            )
