from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any
from uuid import uuid4

from a2a.helpers import (
    get_data_parts,
    new_data_part,
    new_task_from_user_message,
    new_text_message,
    new_text_part,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState

from .deepseek import DeepSeekClient
from .calendar_tool import check_availability
from .matching import analyze_pair
from .models import AgentProfile


SENSITIVE_KEYS = {
    "email",
    "phone",
    "wechat",
    "contact",
    "legalname",
    "pricing",
    "contract",
}


def _contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = key.replace("_", "").lower()
            if normalized in SENSITIVE_KEYS or _contains_sensitive_key(child):
                return True
    if isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


class OPCDecisionEngine:
    def __init__(
        self,
        profile: AgentProfile,
        profiles: dict[str, AgentProfile],
        deepseek_client: DeepSeekClient | None = None,
    ) -> None:
        self.profile = profile
        self.profiles = profiles
        self.deepseek_client = deepseek_client

    async def respond(self, request: dict[str, Any]) -> dict[str, Any]:
        if _contains_sensitive_key(request):
            raise ValueError("Sensitive fields are not allowed in automated screening")

        if request.get("protocol") == "opc.scheduling.v1":
            return self._respond_to_schedule(request)

        sender_id = str(request.get("senderAgentId", ""))
        recipient_id = str(request.get("recipientAgentId", ""))
        round_number = int(request.get("round", 0))
        if recipient_id != self.profile.id:
            raise ValueError("Message recipient does not match this Agent")
        if sender_id not in self.profiles:
            raise ValueError("Unknown sending Agent")
        if round_number not in {1, 2, 3}:
            raise ValueError("Unsupported screening round")

        sender = self.profiles[sender_id]
        report = analyze_pair(sender, self.profile)
        intents = {
            1: "evaluate_collaboration",
            2: "answer_screening",
            3: "propose_introduction",
        }
        summaries = {
            1: f"{self.profile.name}已核对项目方向、供需和协作节奏。",
            2: f"{self.profile.name}已回应候选方的问题并标记边界。",
            3: f"{self.profile.name}认为可以由双方本人决定是否认识。",
        }
        if self.deepseek_client:
            decision, usage = await self.deepseek_client.generate_agent_decision(
                receiver=self.profile,
                sender=sender,
                request=request,
                baseline=report,
            )
            summary = decision.summary
            short_message = decision.short_message
            signals = {
                "commonGround": decision.common_ground,
                "complementarity": decision.complementarity,
                "risks": decision.risks,
                "questions": decision.questions,
            }
            decision_engine = {
                "provider": self.deepseek_client.provider,
                "model": self.deepseek_client.model,
                "usage": usage.model_dump(by_alias=True),
            }
        else:
            summary = summaries[round_number]
            short_message = summaries[round_number]
            signals = {
                "commonGround": report.common_ground,
                "complementarity": report.complementarity,
                "risks": report.risks,
                "questions": report.unconfirmed[:2],
            }
            decision_engine = {"provider": "rules", "model": None}

        return {
            "protocol": "opc.screening.v1",
            "conversationId": request.get("conversationId"),
            "round": round_number,
            "senderAgentId": self.profile.id,
            "recipientAgentId": sender_id,
            "intent": intents[round_number],
            "summary": summary,
            "shortMessage": short_message,
            "disclosedProfile": self.profile.a2a_packet(),
            "signals": signals,
            "decisionEngine": decision_engine,
            "humanConfirmationRequired": True,
        }

    def _respond_to_schedule(self, request: dict[str, Any]) -> dict[str, Any]:
        sender_id = str(request.get("senderAgentId", ""))
        recipient_id = str(request.get("recipientAgentId", ""))
        round_number = int(request.get("round", 0))
        if recipient_id != self.profile.id:
            raise ValueError("Message recipient does not match this Agent")
        if sender_id not in self.profiles:
            raise ValueError("Unknown sending Agent")
        if round_number not in {1, 2, 3}:
            raise ValueError("Unsupported scheduling round")

        requested_start = datetime.fromisoformat(
            str(request["requestedStart"]).replace("Z", "+00:00")
        )
        duration_minutes = int(request["durationMinutes"])
        availability = check_availability(
            self.profile.id,
            requested_start,
            duration_minutes,
        )
        time_label = availability.start.strftime("%H:%M")
        if availability.available and round_number == 1:
            message = f"{self.profile.name}的 Agent 已查询日历：{time_label} 有空。你这边也确认一下。"
        elif availability.available and round_number == 2:
            message = f"{self.profile.name}的 Agent 也确认 {time_label} 有空，可以发起暂定会面。"
        elif availability.available:
            message = f"双方 Agent 已确认 {time_label} 都有空，已生成暂定会面。"
        else:
            message = f"{self.profile.name}在 {time_label} 暂时没有空。"

        return {
            "protocol": "opc.scheduling.v1",
            "conversationId": request.get("conversationId"),
            "round": round_number,
            "senderAgentId": self.profile.id,
            "recipientAgentId": sender_id,
            "intent": "availability_response",
            "message": message,
            "availability": {
                "status": availability.status.value,
                "requestedStart": availability.start.isoformat(),
                "requestedEnd": availability.end.isoformat(),
                "timezone": "Asia/Shanghai",
                "alternatives": [
                    candidate.isoformat() for candidate in availability.alternatives
                ],
            },
            "agentConfirmed": availability.available,
            "tentativeHold": availability.available and round_number == 3,
            "humanConfirmationRequired": True,
            "decisionEngine": {
                "provider": "calendar_tool",
                "tool": "check_availability",
            },
        }

    async def stream_live_schedule(
        self,
        request: dict[str, Any],
    ) -> AsyncIterator[str]:
        speaker_id = str(request.get("speakerAgentId", ""))
        other_id = str(request.get("otherAgentId", ""))
        turn = int(request.get("turn", 0))
        if speaker_id != self.profile.id:
            raise ValueError("Live message speaker does not match this Agent")
        if other_id not in self.profiles:
            raise ValueError("Unknown other Agent")
        if turn not in {1, 2, 3, 4}:
            raise ValueError("Unsupported live conversation turn")

        other = self.profiles[other_id]
        availability_status = str(request["availabilityStatus"])
        if self.deepseek_client:
            async for chunk in self.deepseek_client.stream_schedule_message(
                speaker=self.profile,
                other=other,
                turn=turn,
                requested_start=str(request["requestedStart"]),
                duration_minutes=int(request["durationMinutes"]),
                topic=str(request["topic"]),
                availability_status=availability_status,
                other_availability_status=(
                    str(request["otherAvailabilityStatus"])
                    if request.get("otherAvailabilityStatus")
                    else None
                ),
                previous_message=request.get("previousMessage"),
            ):
                yield chunk
            return

        time_label = datetime.fromisoformat(
            str(request["requestedStart"]).replace("Z", "+00:00")
        ).strftime("%H:%M")
        if availability_status != "AVAILABLE":
            text = f"我刚查过日历，今天 {time_label} 暂时没有空，暂时无法确认这次沟通。"
        else:
            messages = {
                1: f"你好，想问一下今天 {time_label} 是否方便？我们聊聊{request['topic']}。",
                2: f"可以，我查过日历，今天 {time_label} 有空。你这边也确认了吗？",
                3: f"我这边也有空，那就先暂定今天 {time_label}，等待双方本人确认。",
                4: f"好的，双方 Agent 都确认可用，已生成 {time_label} 的暂定会面。",
            }
            text = messages[turn]
        for offset in range(0, len(text), 4):
            yield text[offset : offset + 4]


class OPCAgentExecutor(AgentExecutor):
    def __init__(self, engine: OPCDecisionEngine) -> None:
        self.engine = engine

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        task = context.current_task or new_task_from_user_message(context.message)
        if not context.current_task:
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(
            event_queue=event_queue,
            task_id=task.id,
            context_id=task.context_id,
        )
        await updater.update_status(
            TaskState.TASK_STATE_WORKING,
            message=new_text_message("OPC Agent is evaluating the request."),
        )
        try:
            data_parts = get_data_parts(context.message.parts)
            if len(data_parts) != 1 or not isinstance(data_parts[0], dict):
                raise ValueError("A single structured JSON payload is required")
            request = data_parts[0]
            if request.get("protocol") == "opc.live_schedule.v1":
                await self._stream_live_response(request, updater)
                return
            response = await self.engine.respond(request)
        except Exception as exc:
            await updater.update_status(
                TaskState.TASK_STATE_FAILED,
                message=new_text_message(str(exc)),
            )
            return

        await updater.add_artifact(
            parts=[new_data_part(response, media_type="application/json")],
            name="opc-screening-round",
            last_chunk=True,
        )
        await updater.update_status(
            TaskState.TASK_STATE_COMPLETED,
            message=new_text_message("OPC screening round completed."),
        )

    async def _stream_live_response(
        self,
        request: dict[str, Any],
        updater: TaskUpdater,
    ) -> None:
        artifact_id = str(uuid4())
        pending: str | None = None
        first_chunk = True
        async for chunk in self.engine.stream_live_schedule(request):
            if pending is not None:
                await updater.add_artifact(
                    parts=[new_text_part(pending, media_type="text/plain")],
                    artifact_id=artifact_id,
                    name="live-agent-message",
                    append=not first_chunk,
                    last_chunk=False,
                )
                first_chunk = False
            pending = chunk
        if pending is None:
            raise ValueError("Agent produced an empty live message")
        await updater.add_artifact(
            parts=[new_text_part(pending, media_type="text/plain")],
            artifact_id=artifact_id,
            name="live-agent-message",
            append=not first_chunk,
            last_chunk=True,
        )
        await updater.update_status(
            TaskState.TASK_STATE_COMPLETED,
            message=new_text_message("Live Agent message completed."),
        )

    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        raise NotImplementedError("Cancellation is not supported")
