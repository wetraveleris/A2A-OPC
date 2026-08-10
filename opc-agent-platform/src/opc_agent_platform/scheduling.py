from __future__ import annotations

import asyncio

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .conversation import A2ACommunicator
from .models import (
    CreateScheduleInquiryRequest,
    ScheduleRecord,
    ScheduleState,
    TranscriptTurn,
)
from .profiles import get_profile


class ScheduleStore:
    def __init__(self) -> None:
        self._records: dict[str, ScheduleRecord] = {}
        self._lock = asyncio.Lock()

    async def create(self, request: CreateScheduleInquiryRequest) -> ScheduleRecord:
        record = ScheduleRecord(
            id=str(uuid4()),
            from_agent_id=request.from_agent_id,
            to_agent_id=request.to_agent_id,
            requested_start=request.requested_start,
            duration_minutes=request.duration_minutes,
            topic=request.topic,
            state=ScheduleState.CREATED,
        )
        async with self._lock:
            self._records[record.id] = record
        return record.model_copy(deep=True)

    async def get(self, schedule_id: str) -> ScheduleRecord:
        async with self._lock:
            record = self._records.get(schedule_id)
            if record is None:
                raise KeyError(f"Unknown schedule inquiry: {schedule_id}")
            return record.model_copy(deep=True)

    async def set_state(
        self,
        schedule_id: str,
        state: ScheduleState,
        *,
        message: str | None = None,
        agents_confirmed: bool | None = None,
        alternatives: list[datetime] | None = None,
        error: str | None = None,
    ) -> ScheduleRecord:
        async with self._lock:
            record = self._records[schedule_id]
            record.state = state
            if message is not None:
                record.message = message
            if agents_confirmed is not None:
                record.agents_confirmed = agents_confirmed
            if alternatives is not None:
                record.alternatives = alternatives
            record.error = error
            record.updated_at = datetime.now(timezone.utc)
            return record.model_copy(deep=True)

    async def add_turn(self, schedule_id: str, turn: TranscriptTurn) -> None:
        async with self._lock:
            record = self._records[schedule_id]
            record.transcript.append(turn)
            record.updated_at = datetime.now(timezone.utc)

    async def confirm(
        self,
        schedule_id: str,
        agent_id: str,
        decision: str,
    ) -> ScheduleRecord:
        async with self._lock:
            record = self._records[schedule_id]
            if agent_id not in {record.from_agent_id, record.to_agent_id}:
                raise ValueError("Only a schedule participant can confirm")
            if decision == "decline":
                record.state = ScheduleState.DECLINED
                record.message = "一方本人暂未确认这个时间。"
            else:
                if agent_id not in record.confirmations:
                    record.confirmations.append(agent_id)
                record.state = (
                    ScheduleState.CONFIRMED
                    if len(record.confirmations) == 2
                    else ScheduleState.WAITING_HUMAN_CONFIRMATION
                )
                record.message = (
                    "双方本人已确认会面。"
                    if record.state == ScheduleState.CONFIRMED
                    else "你已确认，等待对方本人确认。"
                )
            record.updated_at = datetime.now(timezone.utc)
            return record.model_copy(deep=True)


class ScheduleService:
    def __init__(self, store: ScheduleStore, communicator: A2ACommunicator) -> None:
        self.store = store
        self.communicator = communicator

    async def start(
        self,
        request: CreateScheduleInquiryRequest,
    ) -> ScheduleRecord:
        source = get_profile(request.from_agent_id)
        target = get_profile(request.to_agent_id)
        if source.id == target.id:
            raise ValueError("An Agent cannot schedule with itself")

        record = await self.store.create(request)
        try:
            first = self._request(
                record,
                round_number=1,
                sender_id=source.id,
                recipient_id=target.id,
                intent="check_availability",
            )
            await self.store.set_state(record.id, ScheduleState.CHECKING_TARGET)
            first_response = await self._exchange(record.id, first)
            if first_response["availability"]["status"] != "AVAILABLE":
                return await self._no_common_slot(record.id, first_response)

            second = self._request(
                record,
                round_number=2,
                sender_id=target.id,
                recipient_id=source.id,
                intent="confirm_requester_availability",
                previous_response=first_response,
            )
            await self.store.set_state(record.id, ScheduleState.CHECKING_REQUESTER)
            second_response = await self._exchange(record.id, second)
            if second_response["availability"]["status"] != "AVAILABLE":
                return await self._no_common_slot(record.id, second_response)

            third = self._request(
                record,
                round_number=3,
                sender_id=source.id,
                recipient_id=target.id,
                intent="create_tentative_hold",
                previous_response=second_response,
            )
            third_response = await self._exchange(record.id, third)
            if third_response["availability"]["status"] != "AVAILABLE":
                return await self._no_common_slot(record.id, third_response)

            await self.store.set_state(
                record.id,
                ScheduleState.AGENTS_CONFIRMED,
                agents_confirmed=True,
                message="双方 Agent 已确认这个时间都有空。",
            )
            return await self.store.set_state(
                record.id,
                ScheduleState.WAITING_HUMAN_CONFIRMATION,
                agents_confirmed=True,
                message="双方 Agent 已确认 15:00 有空，等待两位本人确认。",
            )
        except Exception as exc:
            await self.store.set_state(
                record.id,
                ScheduleState.FAILED,
                error=str(exc),
                message="Agent 时间确认失败。",
            )
            raise

    async def _exchange(
        self,
        schedule_id: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        task_id, task_state, response = await self.communicator.send(
            str(request["recipientAgentId"]), request
        )
        await self.store.add_turn(
            schedule_id,
            TranscriptTurn(
                round=int(request["round"]),
                from_agent_id=str(request["senderAgentId"]),
                to_agent_id=str(request["recipientAgentId"]),
                task_id=task_id,
                task_state=task_state,
                request=request,
                response=response,
            ),
        )
        return response

    async def _no_common_slot(
        self,
        schedule_id: str,
        response: dict[str, Any],
    ) -> ScheduleRecord:
        alternatives = [
            datetime.fromisoformat(value)
            for value in response["availability"].get("alternatives", [])
        ]
        return await self.store.set_state(
            schedule_id,
            ScheduleState.NO_COMMON_SLOT,
            agents_confirmed=False,
            alternatives=alternatives,
            message="这个时间有冲突，Agent 已返回备选时间。",
        )

    @staticmethod
    def _request(
        record: ScheduleRecord,
        round_number: int,
        sender_id: str,
        recipient_id: str,
        intent: str,
        previous_response: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "protocol": "opc.scheduling.v1",
            "conversationId": record.id,
            "round": round_number,
            "senderAgentId": sender_id,
            "recipientAgentId": recipient_id,
            "intent": intent,
            "requestedStart": record.requested_start.isoformat(),
            "durationMinutes": record.duration_minutes,
            "timezone": record.timezone,
            "topic": record.topic,
            "humanConfirmationRequired": True,
        }
        if previous_response is not None:
            payload["previousResponse"] = previous_response
        return payload
