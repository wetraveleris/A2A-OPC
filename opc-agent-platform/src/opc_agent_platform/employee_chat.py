from __future__ import annotations

import os
from copy import deepcopy
from typing import Any
from uuid import uuid4

from .conversation import A2ACommunicator
from .models import (
    CreateEmployeeChatRequest,
    EmployeeChatContext,
    EmployeeChatContextPatch,
    EmployeeChatRecord,
    EmployeeChatTurn,
)
from .profiles import get_profile


def _append_unique(target: list[str], values: list[str]) -> None:
    for value in values:
        cleaned = value.strip()
        if cleaned and cleaned not in target:
            target.append(cleaned)


def apply_context_patch(
    context: EmployeeChatContext,
    patch: EmployeeChatContextPatch,
) -> EmployeeChatContext:
    updated = context.model_copy(deep=True)
    _append_unique(updated.known_facts, patch.known_facts_add)
    _append_unique(updated.decisions, patch.decisions_add)
    resolved = set(patch.open_questions_resolved)
    updated.open_questions = [
        question for question in updated.open_questions if question not in resolved
    ]
    _append_unique(updated.open_questions, patch.open_questions_add)
    return updated


class EmployeeChatStore:
    def __init__(self) -> None:
        self._records: dict[str, EmployeeChatRecord] = {}

    async def put(self, record: EmployeeChatRecord) -> EmployeeChatRecord:
        self._records[record.id] = record
        return record

    async def get(self, conversation_id: str) -> EmployeeChatRecord:
        try:
            return self._records[conversation_id]
        except KeyError as exc:
            raise KeyError(f"Unknown employee chat: {conversation_id}") from exc


class EmployeeChatService:
    def __init__(
        self,
        store: EmployeeChatStore,
        communicator: A2ACommunicator,
    ) -> None:
        self.store = store
        self.communicator = communicator

    def agent_url(self, agent_id: str) -> str:
        environment_key = "OPC_EMPLOYEE_AGENT_URL_" + agent_id.upper().replace(
            "-", "_"
        )
        configured = os.getenv(environment_key, "").strip().rstrip("/")
        if configured:
            return configured
        return f"{self.communicator.base_url}/a2a/{agent_id}"

    async def create(self, request: CreateEmployeeChatRequest) -> EmployeeChatRecord:
        source = get_profile(request.from_agent_id)
        target = get_profile(request.to_agent_id)
        if source.id == target.id:
            raise ValueError("Employee chat requires two different Agents")

        conversation_id = str(uuid4())
        context = EmployeeChatContext(
            goal=request.goal.strip(),
            known_facts=[
                f"{source.name}代表的项目：{source.project_summary}",
                f"{target.name}代表的项目：{target.project_summary}",
            ],
            open_questions=["双方能否形成一个明确、低风险的下一步"],
        )
        record = EmployeeChatRecord(
            id=conversation_id,
            from_agent_id=source.id,
            to_agent_id=target.id,
            goal=context.goal,
            state="COMPLETED",
            context=context,
        )

        previous_message = context.goal
        previous_speaker = source.id
        try:
            for turn_number in range(1, request.max_turns + 1):
                recipient_id = target.id if previous_speaker == source.id else source.id
                context_before = context.model_copy(deep=True)
                payload: dict[str, Any] = {
                    "protocol": "opc.employee_chat.v1",
                    "conversationId": conversation_id,
                    "turn": turn_number,
                    "senderAgentId": previous_speaker,
                    "recipientAgentId": recipient_id,
                    "message": previous_message,
                    "sharedContext": context_before.model_dump(by_alias=True),
                    "privateContextPolicy": {
                        "shareContact": False,
                        "shareCredentials": False,
                        "ownerCommitmentAllowed": False,
                    },
                }
                agent_url = self.agent_url(recipient_id)
                task_id, task_state, response = await self.communicator.send_json_to_url(
                    agent_url,
                    payload,
                )
                patch = EmployeeChatContextPatch.model_validate(
                    response.get("contextPatch", {})
                )
                context = apply_context_patch(context, patch)
                record.turns.append(
                    EmployeeChatTurn(
                        turn=turn_number,
                        from_agent_id=previous_speaker,
                        to_agent_id=recipient_id,
                        agent_card_url=(
                            f"{agent_url}/.well-known/agent-card.json"
                        ),
                        jsonrpc_url=f"{agent_url}/",
                        task_id=task_id,
                        task_state=task_state,
                        request=deepcopy(payload),
                        response=deepcopy(response),
                        context_before=context_before,
                        context_after=context.model_copy(deep=True),
                    )
                )
                previous_speaker = recipient_id
                previous_message = str(response["reply"])
        except Exception as exc:
            record.state = "FAILED"
            record.error = str(exc)
            record.context = context
            await self.store.put(record)
            raise

        record.context = context
        return await self.store.put(record)
