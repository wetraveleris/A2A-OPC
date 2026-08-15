from __future__ import annotations

import json

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

from a2a.client import ClientConfig, create_client
from a2a.helpers import (
    get_artifact_text,
    get_data_parts,
    get_message_text,
    new_data_message,
    new_text_message,
)
from a2a.types import Role, SendMessageRequest, TaskState

from .deepseek import DeepSeekClient
from .matching import analyze_pair
from .models import (
    MatchReport,
    ModelUsage,
    ScreeningRecord,
    ScreeningState,
    TranscriptTurn,
)
from .profiles import get_profile
from .relay import RelayHub
from .store import ScreeningStore


class A2AProtocolError(RuntimeError):
    pass


@dataclass(frozen=True)
class A2ATextEvent:
    kind: str
    task_id: str
    text: str = ""
    task_state: str = ""


@dataclass(frozen=True)
class A2ATaskTextResult:
    task_id: str
    task_state: str
    text: str
    data: dict[str, Any] | None = None


def _artifact_to_text(artifact: Any) -> tuple[str, dict[str, Any] | None]:
    text = (get_artifact_text(artifact) or "").strip()
    if text:
        return text, None

    data_parts = get_data_parts(artifact.parts)
    if not data_parts:
        raise A2AProtocolError("Agent result Artifact did not include text or JSON")
    if len(data_parts) == 1 and isinstance(data_parts[0], dict):
        data = data_parts[0]
        return json.dumps(data, ensure_ascii=False, indent=2), data
    return json.dumps(data_parts, ensure_ascii=False, indent=2), None


class A2ACommunicator:
    def __init__(
        self,
        base_url: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.transport = transport

    async def send(
        self,
        target_agent_id: str,
        payload: dict[str, Any],
    ) -> tuple[str, str, dict[str, Any]]:
        target_url = f"{self.base_url}/a2a/{target_agent_id}"
        async with httpx.AsyncClient(
            transport=self.transport,
            timeout=60.0,
            follow_redirects=True,
        ) as http_client:
            client = await create_client(
                agent=target_url,
                client_config=ClientConfig(
                    streaming=False,
                    httpx_client=http_client,
                    accepted_output_modes=["application/json"],
                ),
            )
            events = [
                event
                async for event in client.send_message(
                    SendMessageRequest(
                        message=new_data_message(
                            payload,
                            media_type="application/json",
                            role=Role.ROLE_USER,
                        )
                    )
                )
            ]

        if len(events) != 1 or not events[0].HasField("task"):
            raise A2AProtocolError("Agent did not return a completed Task")
        task = events[0].task
        state_name = TaskState.Name(task.status.state)
        if task.status.state != TaskState.TASK_STATE_COMPLETED:
            detail = (
                get_message_text(task.status.message)
                if task.status.HasField("message")
                else "no error detail"
            )
            raise A2AProtocolError(f"Agent Task ended in {state_name}: {detail}")
        if len(task.artifacts) != 1:
            raise A2AProtocolError("Agent Task must contain one result Artifact")
        data_parts = get_data_parts(task.artifacts[0].parts)
        if len(data_parts) != 1 or not isinstance(data_parts[0], dict):
            raise A2AProtocolError("Agent result Artifact is not structured JSON")
        return task.id, state_name, data_parts[0]

    async def send_text_to_url(
        self,
        agent_url: str,
        prompt: str,
    ) -> A2ATaskTextResult:
        async with httpx.AsyncClient(
            transport=self.transport,
            timeout=60.0,
            follow_redirects=True,
        ) as http_client:
            client = await create_client(
                agent=agent_url.rstrip("/"),
                client_config=ClientConfig(
                    streaming=False,
                    httpx_client=http_client,
                    accepted_output_modes=["text/plain", "application/json"],
                ),
            )
            events = [
                event
                async for event in client.send_message(
                    SendMessageRequest(
                        message=new_text_message(
                            prompt,
                            media_type="text/plain",
                            role=Role.ROLE_USER,
                        )
                    )
                )
            ]

        if len(events) != 1 or not events[0].HasField("task"):
            raise A2AProtocolError("Agent did not return a completed Task")
        task = events[0].task
        state_name = TaskState.Name(task.status.state)
        if task.status.state != TaskState.TASK_STATE_COMPLETED:
            detail = (
                get_message_text(task.status.message)
                if task.status.HasField("message")
                else "no error detail"
            )
            raise A2AProtocolError(f"Agent Task ended in {state_name}: {detail}")
        if not task.artifacts:
            raise A2AProtocolError("Agent Task did not include a result Artifact")
        text, data = _artifact_to_text(task.artifacts[0])
        return A2ATaskTextResult(
            task_id=task.id,
            task_state=state_name,
            text=text,
            data=data,
        )

    async def send_json_to_url(
        self,
        agent_url: str,
        payload: dict[str, Any],
    ) -> tuple[str, str, dict[str, Any]]:
        async with httpx.AsyncClient(
            transport=self.transport,
            timeout=60.0,
            follow_redirects=True,
        ) as http_client:
            client = await create_client(
                agent=agent_url.rstrip("/"),
                client_config=ClientConfig(
                    streaming=False,
                    httpx_client=http_client,
                    accepted_output_modes=["application/json"],
                ),
            )
            events = [
                event
                async for event in client.send_message(
                    SendMessageRequest(
                        message=new_data_message(
                            payload,
                            media_type="application/json",
                            role=Role.ROLE_USER,
                        )
                    )
                )
            ]

        if len(events) != 1 or not events[0].HasField("task"):
            raise A2AProtocolError("Agent did not return a completed Task")
        task = events[0].task
        state_name = TaskState.Name(task.status.state)
        if task.status.state != TaskState.TASK_STATE_COMPLETED:
            detail = (
                get_message_text(task.status.message)
                if task.status.HasField("message")
                else "no error detail"
            )
            raise A2AProtocolError(f"Agent Task ended in {state_name}: {detail}")
        if len(task.artifacts) != 1:
            raise A2AProtocolError("Agent Task must contain one result Artifact")
        data_parts = get_data_parts(task.artifacts[0].parts)
        if len(data_parts) != 1 or not isinstance(data_parts[0], dict):
            raise A2AProtocolError("Agent result Artifact is not structured JSON")
        return task.id, state_name, data_parts[0]

    async def stream_text(
        self,
        target_agent_id: str,
        payload: dict[str, Any],
    ) -> AsyncIterator[A2ATextEvent]:
        target_url = f"{self.base_url}/a2a/{target_agent_id}"
        task_id = ""
        async with httpx.AsyncClient(
            transport=self.transport,
            timeout=60.0,
            follow_redirects=True,
        ) as http_client:
            client = await create_client(
                agent=target_url,
                client_config=ClientConfig(
                    streaming=True,
                    httpx_client=http_client,
                    accepted_output_modes=["text/plain"],
                ),
            )
            async for event in client.send_message(
                SendMessageRequest(
                    message=new_data_message(
                        payload,
                        media_type="application/json",
                        role=Role.ROLE_USER,
                    )
                )
            ):
                if event.HasField("task"):
                    task_id = event.task.id
                    continue
                if event.HasField("artifact_update"):
                    task_id = event.artifact_update.task_id or task_id
                    text = get_artifact_text(event.artifact_update.artifact)
                    if text:
                        yield A2ATextEvent("delta", task_id, text=text)
                    continue
                if event.HasField("status_update"):
                    task_id = event.status_update.task_id or task_id
                    state = event.status_update.status.state
                    state_name = TaskState.Name(state)
                    if state == TaskState.TASK_STATE_COMPLETED:
                        yield A2ATextEvent(
                            "completed",
                            task_id,
                            task_state=state_name,
                        )
                    elif state in {
                        TaskState.TASK_STATE_FAILED,
                        TaskState.TASK_STATE_REJECTED,
                        TaskState.TASK_STATE_CANCELED,
                    }:
                        detail = (
                            get_message_text(event.status_update.status.message)
                            if event.status_update.status.HasField("message")
                            else "no error detail"
                        )
                        raise A2AProtocolError(
                            f"Streaming Agent Task ended in {state_name}: {detail}"
                        )


class ScreeningService:
    def __init__(
        self,
        store: ScreeningStore,
        communicator: A2ACommunicator,
        deepseek_client: DeepSeekClient | None = None,
        relay_hub: RelayHub | None = None,
    ) -> None:
        self.store = store
        self.communicator = communicator
        self.deepseek_client = deepseek_client
        self.relay_hub = relay_hub

    async def start(
        self,
        from_agent_id: str,
        to_agent_id: str,
        use_relay: bool = False,
        source_profile: AgentProfile | None = None,
        target_profile: AgentProfile | None = None,
    ) -> ScreeningRecord:
        source = source_profile or get_profile(from_agent_id)
        target = target_profile or get_profile(to_agent_id)
        if source.id != from_agent_id or target.id != to_agent_id:
            raise ValueError("Agent profile does not match its bound device")
        if source.id == target.id:
            raise ValueError("An Agent cannot screen itself")
        if use_relay:
            if self.relay_hub is None:
                raise ValueError("Relay is not available")
            offline = [
                agent_id
                for agent_id in (source.id, target.id)
                if not self.relay_hub.is_online(agent_id)
            ]
            if offline:
                raise ValueError(f"Relay Agent offline: {', '.join(offline)}")

        record = await self.store.create(source.id, target.id)
        await self.store.set_state(record.id, ScreeningState.SCREENING)
        try:
            first_request = self._request(
                record.id,
                1,
                source.id,
                target.id,
                "introduce_opc",
                source.a2a_packet(),
                target.a2a_packet(),
            )
            first_response = await self._exchange(
                record.id, first_request, use_relay=use_relay
            )

            second_request = self._request(
                record.id,
                2,
                target.id,
                source.id,
                "answer_screening",
                target.a2a_packet(),
                source.a2a_packet(),
                first_response,
            )
            second_response = await self._exchange(
                record.id, second_request, use_relay=use_relay
            )

            third_request = self._request(
                record.id,
                3,
                source.id,
                target.id,
                "propose_introduction",
                source.a2a_packet(),
                target.a2a_packet(),
                second_response,
            )
            await self._exchange(record.id, third_request, use_relay=use_relay)

            completed = await self.store.get(record.id)
            baseline = analyze_pair(source, target)
            if self.deepseek_client:
                decision, synthesis_usage = (
                    await self.deepseek_client.synthesize_report(
                        source=source,
                        target=target,
                        transcript=completed.transcript,
                        baseline=baseline,
                    )
                )
                total_usage = self._total_usage(completed, synthesis_usage)
                report = MatchReport(
                    recommendation=decision.recommendation,
                    confidence=decision.confidence,
                    score=decision.score,
                    summary=decision.summary,
                    common_ground=decision.common_ground,
                    complementarity=decision.complementarity,
                    risks=decision.risks,
                    unconfirmed=decision.unconfirmed,
                    generated_by=self.deepseek_client.provider,
                    model=self.deepseek_client.model,
                    token_usage=total_usage,
                )
            else:
                report = baseline
            report.evidence_task_ids = [turn.task_id for turn in completed.transcript]
            await self.store.set_report(record.id, report)
            await self.store.set_state(record.id, ScreeningState.REPORT_GENERATED)
            return await self.store.set_state(
                record.id, ScreeningState.WAITING_OWNER_APPROVAL
            )
        except Exception as exc:
            await self.store.set_state(
                record.id,
                ScreeningState.FAILED,
                error=str(exc),
            )
            raise

    async def _exchange(
        self,
        screening_id: str,
        request: dict[str, Any],
        use_relay: bool = False,
    ) -> dict[str, Any]:
        await self.store.set_state(
            screening_id, ScreeningState.WAITING_REMOTE_AGENT
        )
        recipient_id = str(request["recipientAgentId"])
        if use_relay:
            if self.relay_hub is None:
                raise ValueError("Relay is not available")
            task_id, task_state, response = await self.relay_hub.dispatch(
                recipient_id, request
            )
        else:
            task_id, task_state, response = await self.communicator.send(
                recipient_id, request
            )
        await self.store.add_turn(
            screening_id,
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
        await self.store.set_state(screening_id, ScreeningState.SCREENING)
        return response

    @staticmethod
    def _total_usage(
        screening: ScreeningRecord,
        synthesis_usage: ModelUsage,
    ) -> ModelUsage:
        usage = synthesis_usage.model_copy()
        for turn in screening.transcript:
            raw = turn.response.get("decisionEngine", {}).get("usage", {})
            usage.prompt_tokens += int(raw.get("promptTokens", 0))
            usage.completion_tokens += int(raw.get("completionTokens", 0))
            usage.reasoning_tokens += int(raw.get("reasoningTokens", 0))
            usage.total_tokens += int(raw.get("totalTokens", 0))
            usage.calls += int(raw.get("calls", 0))
        return usage

    @staticmethod
    def _request(
        conversation_id: str,
        round_number: int,
        sender_id: str,
        recipient_id: str,
        intent: str,
        disclosed_profile: dict[str, Any],
        recipient_profile: dict[str, Any],
        previous_response: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "protocol": "opc.screening.v1",
            "conversationId": conversation_id,
            "round": round_number,
            "senderAgentId": sender_id,
            "recipientAgentId": recipient_id,
            "intent": intent,
            "disclosedProfile": disclosed_profile,
            "recipientProfile": recipient_profile,
            "humanConfirmationRequired": True,
        }
        if previous_response is not None:
            payload["previousResponse"] = previous_response
        return payload
