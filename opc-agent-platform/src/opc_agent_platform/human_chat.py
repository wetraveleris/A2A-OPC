from __future__ import annotations

import asyncio
import os
import re
import secrets

from collections.abc import AsyncIterator
from copy import deepcopy
from datetime import datetime, timezone
from difflib import SequenceMatcher

from .conversation import A2ACommunicator
from .employee_chat import apply_context_patch
from .models import (
    CreateHumanChatRequest,
    EmployeeChatContext,
    EmployeeChatContextPatch,
    EmployeeChatTurn,
    HumanChatApprovalRequest,
    HumanChatAuditEvent,
    HumanChatCreated,
    HumanChatDirectMessageRequest,
    HumanChatDraft,
    HumanChatMode,
    HumanChatMessage,
    HumanChatMessageSource,
    HumanChatParticipant,
    HumanChatRecord,
    HumanChatRejectionRequest,
    HumanChatStartRequest,
    HumanChatStopRequest,
    HumanChatSwitchModeRequest,
    HumanChatRunPolicy,
    HumanChatState,
    HumanChatTopology,
    HumanChatView,
)
from .profiles import get_profile
from .relay import RelayHub


class HumanChatStore:
    def __init__(self) -> None:
        self._records: dict[str, HumanChatRecord] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._conditions: dict[str, asyncio.Condition] = {}

    async def create(self, record: HumanChatRecord) -> None:
        self._records[record.id] = record
        self._locks[record.id] = asyncio.Lock()
        self._conditions[record.id] = asyncio.Condition()

    def get(self, conversation_id: str) -> HumanChatRecord:
        try:
            return self._records[conversation_id]
        except KeyError as exc:
            raise KeyError(f"Unknown human Agent chat: {conversation_id}") from exc

    def lock(self, conversation_id: str) -> asyncio.Lock:
        self.get(conversation_id)
        return self._locks[conversation_id]

    async def notify(self, conversation_id: str) -> None:
        async with self._conditions[conversation_id]:
            self._conditions[conversation_id].notify_all()

    async def wait_for_change(
        self,
        conversation_id: str,
        version: int,
        timeout: float = 15.0,
    ) -> None:
        condition = self._conditions[conversation_id]
        async with condition:
            if self.get(conversation_id).version != version:
                return
            try:
                await asyncio.wait_for(condition.wait(), timeout=timeout)
            except TimeoutError:
                return


class HumanChatService:
    def __init__(
        self,
        store: HumanChatStore,
        communicator: A2ACommunicator,
        relay_hub: RelayHub | None = None,
    ) -> None:
        self.store = store
        self.communicator = communicator
        self.relay_hub = relay_hub

    @staticmethod
    def agent_url(record: HumanChatRecord, agent_id: str) -> str:
        try:
            return record.agent_urls[agent_id]
        except KeyError as exc:
            raise ValueError(f"Conversation has no endpoint for Agent {agent_id}") from exc

    def _require_relay_nodes_online(self, *agent_ids: str) -> None:
        if self.relay_hub is None:
            raise ValueError("Relay is not available")
        offline = [
            agent_id
            for agent_id in agent_ids
            if not self.relay_hub.is_online(agent_id)
        ]
        if offline:
            raise ValueError(f"Relay Agent offline: {', '.join(offline)}")

    async def create(self, request: CreateHumanChatRequest) -> HumanChatCreated:
        source = request.source_profile or get_profile(request.from_agent_id)
        target = request.target_profile or get_profile(request.to_agent_id)
        if source.id == target.id:
            raise ValueError("Human Agent chat requires two different Agents")

        source_url = f"{self.communicator.base_url}/a2a/{source.id}"
        target_url = f"{self.communicator.base_url}/a2a/{target.id}"
        if request.topology == HumanChatTopology.PUBLIC_A_B:
            if source.id != "opc-builder" or target.id != "shen-zhiye":
                raise ValueError("Public A/B topology requires opc-builder and shen-zhiye")
            target_url = os.getenv("OPC_REMOTE_AGENT_B_URL", "").strip().rstrip("/")
            if not target_url:
                raise ValueError("Computer B public A2A URL is not configured")
        elif request.topology == HumanChatTopology.RELAY_A_B:
            if request.mode != HumanChatMode.HUMAN_DIRECT:
                self._require_relay_nodes_online(source.id, target.id)
            source_url = f"relay://{source.id}"
            target_url = f"relay://{target.id}"

        conversation_id = secrets.token_urlsafe(18)
        token_a = secrets.token_urlsafe(24)
        token_b = secrets.token_urlsafe(24)
        is_takeover = request.mode == HumanChatMode.AGENT_TAKEOVER
        is_direct = request.mode == HumanChatMode.HUMAN_DIRECT
        run_policy = (
            HumanChatRunPolicy.LIMITED
            if request.max_turns is not None
            else request.run_policy
        )
        record = HumanChatRecord(
            id=conversation_id,
            from_agent_id=source.id,
            to_agent_id=target.id,
            goal=request.goal.strip(),
            connection_id=request.connection_id,
            max_turns=request.max_turns,
            run_policy=run_policy,
            mode=request.mode,
            topology=request.topology,
            agent_urls={source.id: source_url, target.id: target_url},
            agent_profiles={source.id: source, target.id: target},
            agent_runtime={
                source.id: dict(request.source_runtime),
                target.id: dict(request.target_runtime),
            },
            state=(
                HumanChatState.AGENT_READY
                if is_takeover
                else HumanChatState.HUMAN_DIRECT
                if is_direct
                else HumanChatState.WAITING_OWNER_A
            ),
            context=(request.initial_context or EmployeeChatContext(
                goal=request.goal.strip(),
                known_facts=[
                    f"{source.name}代表的项目：{source.project_summary}",
                    f"{target.name}代表的项目：{target.project_summary}",
                ],
                open_questions=["双方能否形成一个明确、低风险的下一步"],
            )),
            pending_draft=(
                None
                if is_direct
                else HumanChatDraft(
                    turn=0,
                    speaker_agent_id=source.id,
                    recipient_agent_id=target.id,
                    original_text=request.goal.strip(),
                )
            ),
            access_tokens={source.id: token_a, target.id: token_b},
            audit=[
                HumanChatAuditEvent(
                    sequence=1,
                    action="conversation.created",
                    actor_agent_id=source.id,
                    detail=(
                        "托管会话已创建，等待参与用户在房间内授权启动。"
                        if is_takeover
                        else "人工直聊房间已创建，模型不会自动介入。"
                        if is_direct
                        else "用户 A 创建会话，初始消息等待本人批准。"
                    ),
                )
            ],
        )
        await self.store.create(record)
        page = "/app/agent-room.html"
        return HumanChatCreated(
            id=record.id,
            mode=record.mode,
            state=record.state,
            topology=record.topology,
            agent_a_url=source_url,
            agent_b_url=target_url,
            participant_a_url=f"{page}?room={record.id}&token={token_a}",
            participant_b_url=f"{page}?room={record.id}&token={token_b}",
        )

    def _viewer_agent_id(self, record: HumanChatRecord, token: str) -> str:
        for agent_id, expected in record.access_tokens.items():
            if secrets.compare_digest(token, expected):
                return agent_id
        raise PermissionError("Invalid participant access token")

    def _participant(self, record: HumanChatRecord, agent_id: str) -> HumanChatParticipant:
        profile = record.agent_profiles.get(agent_id) or get_profile(agent_id)
        return HumanChatParticipant(
            side="a" if agent_id == record.from_agent_id else "b",
            agent_id=agent_id,
            agent_name=profile.name,
            role=profile.role,
            computer_name=(
                "电脑 A"
                if record.topology
                in {HumanChatTopology.PUBLIC_A_B, HumanChatTopology.RELAY_A_B}
                and agent_id == record.from_agent_id
                else "电脑 B"
                if record.topology
                in {HumanChatTopology.PUBLIC_A_B, HumanChatTopology.RELAY_A_B}
                else "当前服务"
            ),
            agent_url=self.agent_url(record, agent_id),
        )

    def view(self, conversation_id: str, token: str) -> HumanChatView:
        record = self.store.get(conversation_id)
        viewer_id = self._viewer_agent_id(record, token)
        other_id = (
            record.to_agent_id if viewer_id == record.from_agent_id else record.from_agent_id
        )
        pending_owner = (
            record.pending_draft.speaker_agent_id if record.pending_draft else None
        )
        can_act = pending_owner == viewer_id and record.state in {
            HumanChatState.WAITING_OWNER_A,
            HumanChatState.WAITING_OWNER_B,
        }
        approved_turns = {message.turn for message in record.messages}
        visible_a2a_turns = (
            [turn for turn in record.a2a_turns if turn.turn in approved_turns]
            if record.mode == HumanChatMode.HUMAN_APPROVAL
            else record.a2a_turns
        )
        return HumanChatView(
            id=record.id,
            goal=record.goal,
            state=record.state,
            max_turns=record.max_turns,
            run_policy=record.run_policy,
            mode=record.mode,
            topology=record.topology,
            version=record.version,
            viewer=self._participant(record, viewer_id),
            other=self._participant(record, other_id),
            waiting_for_agent_id=pending_owner,
            can_act=can_act,
            can_start=(
                record.mode == HumanChatMode.AGENT_TAKEOVER
                and record.state
                in {HumanChatState.AGENT_READY, HumanChatState.STOPPED}
            ),
            can_stop=(
                record.mode == HumanChatMode.AGENT_TAKEOVER
                and record.state in {
                    HumanChatState.AGENT_RUNNING,
                    HumanChatState.STOPPING,
                }
            ),
            can_send_direct=(
                record.mode == HumanChatMode.HUMAN_DIRECT
                and record.state == HumanChatState.HUMAN_DIRECT
            ),
            can_switch_to_approval=record.state not in {
                HumanChatState.REJECTED,
                HumanChatState.FAILED,
            },
            pause_reason=record.pause_reason,
            context=record.context.model_copy(deep=True),
            pending_draft=(
                record.pending_draft.model_copy(deep=True) if can_act else None
            ),
            messages=[message.model_copy(deep=True) for message in record.messages],
            a2a_turns=[turn.model_copy(deep=True) for turn in visible_a2a_turns],
            audit=[event.model_copy(deep=True) for event in record.audit],
            error=record.error,
        )

    async def approve(
        self,
        conversation_id: str,
        token: str,
        request: HumanChatApprovalRequest,
    ) -> HumanChatView:
        async with self.store.lock(conversation_id):
            record = self.store.get(conversation_id)
            viewer_id = self._viewer_agent_id(record, token)
            if request.expected_version != record.version:
                raise RuntimeError("Conversation changed; reload before approving")
            draft = record.pending_draft
            if draft is None or draft.speaker_agent_id != viewer_id:
                raise PermissionError("This draft belongs to the other participant")
            if record.state not in {
                HumanChatState.WAITING_OWNER_A,
                HumanChatState.WAITING_OWNER_B,
            }:
                raise ValueError("Conversation is not waiting for approval")

            approved_text = request.message.strip()
            record.messages.append(
                HumanChatMessage(
                    turn=draft.turn,
                    speaker_agent_id=draft.speaker_agent_id,
                    recipient_agent_id=draft.recipient_agent_id,
                    text=approved_text,
                    original_text=draft.original_text,
                    human_edited=approved_text != draft.original_text,
                    source=HumanChatMessageSource.AGENT_APPROVED,
                    approved_by_agent_id=viewer_id,
                    source_task_id=draft.source_task_id,
                    source_task_state=draft.source_task_state,
                )
            )
            record.context = apply_context_patch(record.context, draft.context_patch)
            self._audit(
                record,
                "draft.approved",
                viewer_id,
                "本人修改并批准 Agent 草稿。" if approved_text != draft.original_text else "本人批准 Agent 草稿。",
            )

            if self._limit_reached(record, draft.turn):
                record.pending_draft = None
                record.state = HumanChatState.COMPLETED
                self._touch(record)
                self._audit(record, "conversation.completed", viewer_id, "最终草稿已批准。")
                await self.store.notify(record.id)
                return self.view(record.id, token)

            next_turn = draft.turn + 1
            recipient_id = draft.recipient_agent_id
            context_before = record.context.model_copy(deep=True)
            payload = {
                "protocol": "opc.employee_chat.v1",
                "conversationId": record.id,
                "turn": next_turn,
                "senderAgentId": viewer_id,
                "recipientAgentId": recipient_id,
                "message": approved_text,
                "sharedContext": context_before.model_dump(by_alias=True),
                "privateContextPolicy": {
                    "shareContact": False,
                    "shareCredentials": False,
                    "ownerCommitmentAllowed": False,
                },
            }
            agent_url = self.agent_url(record, recipient_id)
            try:
                task_id, task_state, response = await self._send_to_agent(
                    record,
                    recipient_id,
                    payload,
                )
                patch = EmployeeChatContextPatch.model_validate(
                    response.get("contextPatch", {})
                )
                context_after = apply_context_patch(context_before, patch)
                record.a2a_turns.append(
                    EmployeeChatTurn(
                        turn=next_turn,
                        from_agent_id=viewer_id,
                        to_agent_id=recipient_id,
                        agent_card_url=f"{agent_url}/.well-known/agent-card.json",
                        jsonrpc_url=f"{agent_url}/",
                        task_id=task_id,
                        task_state=task_state,
                        request=deepcopy(payload),
                        response=deepcopy(response),
                        context_before=context_before,
                        context_after=context_after,
                    )
                )
                record.pending_draft = HumanChatDraft(
                    turn=next_turn,
                    speaker_agent_id=recipient_id,
                    recipient_agent_id=viewer_id,
                    original_text=str(response["reply"]),
                    context_patch=patch,
                    source_task_id=task_id,
                    source_task_state=task_state,
                    request=deepcopy(payload),
                    response=deepcopy(response),
                )
                record.state = (
                    HumanChatState.WAITING_OWNER_A
                    if recipient_id == record.from_agent_id
                    else HumanChatState.WAITING_OWNER_B
                )
                self._audit(
                    record,
                    "a2a.task.completed",
                    recipient_id,
                    f"A2A Task {task_id} 生成回复草稿，等待本人批准。",
                )
            except Exception as exc:
                record.pending_draft = None
                record.state = HumanChatState.FAILED
                record.error = str(exc)
                self._audit(record, "a2a.task.failed", recipient_id, str(exc))
                self._touch(record)
                await self.store.notify(record.id)
                raise

            self._touch(record)
            await self.store.notify(record.id)
            return self.view(record.id, token)

    async def start(
        self,
        conversation_id: str,
        token: str,
        request: HumanChatStartRequest,
    ) -> HumanChatView:
        async with self.store.lock(conversation_id):
            record = self.store.get(conversation_id)
            viewer_id = self._viewer_agent_id(record, token)
            if record.mode != HumanChatMode.AGENT_TAKEOVER:
                raise ValueError("Only Agent takeover sessions can be started")
            if record.state not in {
                HumanChatState.AGENT_READY,
                HumanChatState.STOPPED,
            }:
                return self.view(record.id, token)
            if record.topology == HumanChatTopology.RELAY_A_B:
                self._require_relay_nodes_online(
                    record.from_agent_id,
                    record.to_agent_id,
                )
            if record.pending_draft is None:
                self._seed_takeover_from_history(record)
            record.stop_requested = False
            record.requested_mode = None
            record.pause_reason = None
            record.state = HumanChatState.AGENT_RUNNING
            self._audit(record, "takeover.started", viewer_id, request.reason.strip())
            self._touch(record)
            await self.store.notify(record.id)
            view = self.view(record.id, token)
        asyncio.create_task(self._run_takeover(record.id))
        return view

    async def switch_mode(
        self,
        conversation_id: str,
        token: str,
        request: HumanChatSwitchModeRequest,
    ) -> HumanChatView:
        record = self.store.get(conversation_id)
        viewer_id = self._viewer_agent_id(record, token)
        if request.mode == record.mode and record.state != HumanChatState.STOPPED:
            return self.view(record.id, token)
        if record.state in {HumanChatState.REJECTED, HumanChatState.FAILED}:
            raise ValueError("Closed sessions cannot switch mode")
        if (
            record.topology == HumanChatTopology.RELAY_A_B
            and request.mode != HumanChatMode.HUMAN_DIRECT
        ):
            self._require_relay_nodes_online(
                record.from_agent_id,
                record.to_agent_id,
            )

        if record.state in {
            HumanChatState.AGENT_RUNNING,
            HumanChatState.STOPPING,
        }:
            record.stop_requested = True
            record.requested_mode = request.mode
            record.state = HumanChatState.STOPPING
            self._audit(
                record,
                "mode.switch_requested",
                viewer_id,
                request.reason.strip(),
            )
            self._touch(record)
            await self.store.notify(record.id)
            return self.view(record.id, token)

        async with self.store.lock(conversation_id):
            record = self.store.get(conversation_id)
            if (
                request.mode == HumanChatMode.HUMAN_APPROVAL
                and record.messages
                and (
                    record.pending_draft is None
                    or record.pending_draft.already_sent
                )
            ):
                await self._prepare_approval_reply(record)
            else:
                self._apply_mode(record, request.mode)
            self._audit(
                record,
                "mode.switched",
                viewer_id,
                f"沟通模式已切换为 {request.mode.value}。{request.reason.strip()}",
            )
            self._touch(record)
            await self.store.notify(record.id)
            return self.view(record.id, token)

    async def send_direct_message(
        self,
        conversation_id: str,
        token: str,
        request: HumanChatDirectMessageRequest,
    ) -> HumanChatView:
        async with self.store.lock(conversation_id):
            record = self.store.get(conversation_id)
            viewer_id = self._viewer_agent_id(record, token)
            if record.mode != HumanChatMode.HUMAN_DIRECT:
                raise ValueError("Direct messages require human direct mode")
            recipient_id = (
                record.to_agent_id
                if viewer_id == record.from_agent_id
                else record.from_agent_id
            )
            next_turn = self._next_turn(record)
            message = request.message.strip()
            record.pause_reason = None
            record.messages.append(
                HumanChatMessage(
                    turn=next_turn,
                    speaker_agent_id=viewer_id,
                    recipient_agent_id=recipient_id,
                    text=message,
                    original_text=message,
                    human_edited=False,
                    human_approved=True,
                    source=HumanChatMessageSource.HUMAN_DIRECT,
                    approved_by_agent_id=viewer_id,
                )
            )
            record.pending_draft = HumanChatDraft(
                turn=next_turn,
                speaker_agent_id=viewer_id,
                recipient_agent_id=recipient_id,
                original_text=message,
                already_sent=True,
            )
            self._audit(
                record,
                "human.message.sent",
                viewer_id,
                "用户直接发送消息；后续 Agent 将优先回应这条消息。",
            )
            self._touch(record)
            await self.store.notify(record.id)
            return self.view(record.id, token)

    async def stop(
        self,
        conversation_id: str,
        token: str,
        request: HumanChatStopRequest,
    ) -> HumanChatView:
        record = self.store.get(conversation_id)
        viewer_id = self._viewer_agent_id(record, token)
        if record.mode != HumanChatMode.AGENT_TAKEOVER:
            raise ValueError("Only Agent takeover sessions can be stopped")
        if record.state not in {
            HumanChatState.AGENT_RUNNING,
            HumanChatState.STOPPING,
        }:
            return self.view(record.id, token)
        record.stop_requested = True
        record.state = HumanChatState.STOPPING
        self._audit(record, "takeover.stop_requested", viewer_id, request.reason.strip())
        self._touch(record)
        await self.store.notify(record.id)
        return self.view(record.id, token)

    async def _run_takeover(self, conversation_id: str) -> None:
        """Run approved-by-policy turns sequentially so each A2A hop remains auditable."""
        while True:
            async with self.store.lock(conversation_id):
                record = self.store.get(conversation_id)
                if record.stop_requested or record.state == HumanChatState.STOPPING:
                    self._finish_takeover_stop(record)
                    self._touch(record)
                    await self.store.notify(record.id)
                    return
                if record.state in {HumanChatState.COMPLETED, HumanChatState.FAILED}:
                    return
                draft = record.pending_draft
                if draft is None:
                    record.state = HumanChatState.FAILED
                    record.error = "Agent takeover has no pending draft"
                    self._touch(record)
                    await self.store.notify(record.id)
                    return
                message = draft.original_text
                speaker_id = draft.speaker_agent_id
                recipient_id = draft.recipient_agent_id
                if not draft.already_sent:
                    record.messages.append(
                        HumanChatMessage(
                            turn=draft.turn,
                            speaker_agent_id=speaker_id,
                            recipient_agent_id=recipient_id,
                            text=message,
                            original_text=message,
                            human_edited=False,
                            human_approved=False,
                            source=HumanChatMessageSource.AGENT_AUTO,
                            approved_by_agent_id=speaker_id,
                            source_task_id=draft.source_task_id,
                            source_task_state=draft.source_task_state,
                        )
                    )
                    record.context = apply_context_patch(
                        record.context,
                        draft.context_patch,
                    )
                    self._audit(
                        record,
                        "agent.message.sent",
                        speaker_id,
                        "Agent 接管自动发送消息。",
                    )
                if self._limit_reached(record, draft.turn):
                    record.pending_draft = None
                    record.state = HumanChatState.COMPLETED
                    self._touch(record)
                    self._audit(record, "conversation.completed", speaker_id, "Agent 接管完成全部回合。")
                    await self.store.notify(record.id)
                    return
                next_turn = draft.turn + 1
                context_before = record.context.model_copy(deep=True)
                payload = self._payload(record, next_turn, speaker_id, recipient_id, message, context_before)
                agent_url = self.agent_url(record, recipient_id)
                try:
                    task_id, task_state, response = await self._send_to_agent(
                        record,
                        recipient_id,
                        payload,
                    )
                    patch = EmployeeChatContextPatch.model_validate(response.get("contextPatch", {}))
                    context_after = apply_context_patch(context_before, patch)
                    record.a2a_turns.append(EmployeeChatTurn(
                        turn=next_turn,
                        from_agent_id=speaker_id,
                        to_agent_id=recipient_id,
                        agent_card_url=f"{agent_url}/.well-known/agent-card.json",
                        jsonrpc_url=f"{agent_url}/",
                        task_id=task_id,
                        task_state=task_state,
                        request=deepcopy(payload),
                        response=deepcopy(response),
                        context_before=context_before,
                        context_after=context_after,
                    ))
                    action = str(response.get("action", "REPLY")).upper()
                    reply = str(response.get("reply", "")).strip()
                    repeated = bool(reply) and self._is_repeated_reply(record, reply)
                    if (
                        record.run_policy == HumanChatRunPolicy.CONTINUOUS
                        and not record.stop_requested
                        and (action == "STOP" or not reply or repeated)
                    ):
                        reason = (
                            "检测到回复与近期内容重复，Agent 自动暂停。"
                            if repeated
                            else "Agent 判断当前没有新的有用内容需要回复，自动暂停。"
                        )
                        record.pending_draft = None
                        record.state = HumanChatState.STOPPED
                        record.pause_reason = reason
                        self._audit(
                            record,
                            "takeover.paused",
                            recipient_id,
                            reason,
                        )
                        self._touch(record)
                        await self.store.notify(record.id)
                        return
                    record.pending_draft = HumanChatDraft(
                        turn=next_turn,
                        speaker_agent_id=recipient_id,
                        recipient_agent_id=speaker_id,
                        original_text=reply,
                        context_patch=patch,
                        source_task_id=task_id,
                        source_task_state=task_state,
                        request=deepcopy(payload),
                        response=deepcopy(response),
                    )
                    record.state = (
                        HumanChatState.STOPPING
                        if record.stop_requested
                        else HumanChatState.AGENT_RUNNING
                    )
                    detail = (
                        f"A2A Task {task_id} 完成，停止请求已生效，不再发送回复。"
                        if record.stop_requested
                        else f"A2A Task {task_id} 完成，Agent 接管将自动继续。"
                    )
                    self._audit(record, "a2a.task.completed", recipient_id, detail)
                except Exception as exc:
                    record.pending_draft = None
                    if record.stop_requested:
                        self._finish_takeover_stop(record, recipient_id)
                    else:
                        record.state = HumanChatState.FAILED
                        record.error = str(exc)
                        self._audit(record, "a2a.task.failed", recipient_id, str(exc))
                    self._touch(record)
                    await self.store.notify(record.id)
                    return
                self._touch(record)
                await self.store.notify(record.id)
            await asyncio.sleep(0.15)

    @staticmethod
    def _payload(
        record: HumanChatRecord,
        turn: int,
        sender_id: str,
        recipient_id: str,
        message: str,
        context: EmployeeChatContext,
    ) -> dict:
        sender_profile = record.agent_profiles.get(sender_id)
        recipient_profile = record.agent_profiles.get(recipient_id)
        if sender_profile is None:
            sender_profile = get_profile(sender_id)
        if recipient_profile is None:
            recipient_profile = get_profile(recipient_id)
        sender_runtime = record.agent_runtime.get(sender_id, {})
        recipient_runtime = record.agent_runtime.get(recipient_id, {})
        return {
            "protocol": "opc.employee_chat.v1",
            "conversationId": record.id,
            "turn": turn,
            "senderAgentId": sender_id,
            "recipientAgentId": recipient_id,
            "message": message,
            "conversationTopic": record.goal,
            "senderProfile": sender_profile.a2a_packet(),
            "recipientProfile": recipient_profile.a2a_packet(),
            "senderRuntime": sender_runtime,
            "recipientRuntime": recipient_runtime,
            "recentHistory": [
                {
                    "turn": item.turn,
                    "speakerAgentId": item.speaker_agent_id,
                    "message": item.text,
                    "source": item.source.value,
                }
                for item in record.messages[-12:]
            ],
            "sharedContext": context.model_dump(by_alias=True),
            "privateContextPolicy": {
                "shareContact": False,
                "shareCredentials": False,
                "ownerCommitmentAllowed": False,
            },
        }

    async def reject(
        self,
        conversation_id: str,
        token: str,
        request: HumanChatRejectionRequest,
    ) -> HumanChatView:
        async with self.store.lock(conversation_id):
            record = self.store.get(conversation_id)
            viewer_id = self._viewer_agent_id(record, token)
            if request.expected_version != record.version:
                raise RuntimeError("Conversation changed; reload before rejecting")
            if not record.pending_draft or record.pending_draft.speaker_agent_id != viewer_id:
                raise PermissionError("Only the current draft owner can reject")
            record.pending_draft = None
            record.state = HumanChatState.REJECTED
            self._audit(record, "conversation.rejected", viewer_id, request.reason.strip())
            self._touch(record)
            await self.store.notify(record.id)
            return self.view(record.id, token)

    async def stream(
        self,
        conversation_id: str,
        token: str,
    ) -> AsyncIterator[str]:
        version = 0
        while True:
            current = self.view(conversation_id, token)
            if current.version != version:
                yield self._sse("conversation.updated", current)
                version = current.version
            if current.state in {
                HumanChatState.COMPLETED,
                HumanChatState.REJECTED,
                HumanChatState.FAILED,
            }:
                return
            await self.store.wait_for_change(conversation_id, version)
            yield ": keep-alive\n\n"

    @staticmethod
    def _touch(record: HumanChatRecord) -> None:
        record.version += 1
        record.updated_at = datetime.now(timezone.utc)

    @staticmethod
    def _limit_reached(record: HumanChatRecord, turn: int) -> bool:
        return (
            record.run_policy == HumanChatRunPolicy.LIMITED
            and record.max_turns is not None
            and turn >= record.max_turns
        )

    @staticmethod
    def _next_turn(record: HumanChatRecord) -> int:
        message_turn = record.messages[-1].turn if record.messages else -1
        task_turn = record.a2a_turns[-1].turn if record.a2a_turns else -1
        return max(message_turn, task_turn) + 1

    @staticmethod
    def _seed_takeover_from_history(record: HumanChatRecord) -> None:
        if not record.messages:
            record.pending_draft = HumanChatDraft(
                turn=0,
                speaker_agent_id=record.from_agent_id,
                recipient_agent_id=record.to_agent_id,
                original_text=record.goal,
            )
            return
        last = record.messages[-1]
        record.pending_draft = HumanChatDraft(
            turn=last.turn,
            speaker_agent_id=last.speaker_agent_id,
            recipient_agent_id=last.recipient_agent_id,
            original_text=last.text,
            already_sent=True,
        )

    def _apply_mode(
        self,
        record: HumanChatRecord,
        mode: HumanChatMode,
    ) -> None:
        record.stop_requested = False
        record.requested_mode = None
        record.mode = mode
        if mode == HumanChatMode.AGENT_TAKEOVER:
            if record.pending_draft is None:
                self._seed_takeover_from_history(record)
            record.state = HumanChatState.AGENT_READY
        elif mode == HumanChatMode.HUMAN_DIRECT:
            record.pending_draft = None
            record.state = HumanChatState.HUMAN_DIRECT
        else:
            if record.pending_draft is None:
                self._seed_takeover_from_history(record)
                if record.pending_draft:
                    record.pending_draft.already_sent = False
            pending_owner = (
                record.pending_draft.speaker_agent_id
                if record.pending_draft
                else record.from_agent_id
            )
            record.state = (
                HumanChatState.WAITING_OWNER_A
                if pending_owner == record.from_agent_id
                else HumanChatState.WAITING_OWNER_B
            )

    async def _prepare_approval_reply(self, record: HumanChatRecord) -> None:
        last = record.messages[-1]
        sender_id = last.speaker_agent_id
        recipient_id = last.recipient_agent_id
        next_turn = self._next_turn(record)
        context_before = record.context.model_copy(deep=True)
        payload = self._payload(
            record,
            next_turn,
            sender_id,
            recipient_id,
            last.text,
            context_before,
        )
        agent_url = self.agent_url(record, recipient_id)
        task_id, task_state, response = await self._send_to_agent(
            record,
            recipient_id,
            payload,
        )
        patch = EmployeeChatContextPatch.model_validate(
            response.get("contextPatch", {})
        )
        context_after = apply_context_patch(context_before, patch)
        record.a2a_turns.append(
            EmployeeChatTurn(
                turn=next_turn,
                from_agent_id=sender_id,
                to_agent_id=recipient_id,
                agent_card_url=f"{agent_url}/.well-known/agent-card.json",
                jsonrpc_url=f"{agent_url}/",
                task_id=task_id,
                task_state=task_state,
                request=deepcopy(payload),
                response=deepcopy(response),
                context_before=context_before,
                context_after=context_after,
            )
        )
        record.pending_draft = HumanChatDraft(
            turn=next_turn,
            speaker_agent_id=recipient_id,
            recipient_agent_id=sender_id,
            original_text=str(response["reply"]),
            context_patch=patch,
            source_task_id=task_id,
            source_task_state=task_state,
            request=deepcopy(payload),
            response=deepcopy(response),
        )
        record.mode = HumanChatMode.HUMAN_APPROVAL
        record.state = (
            HumanChatState.WAITING_OWNER_A
            if recipient_id == record.from_agent_id
            else HumanChatState.WAITING_OWNER_B
        )
        self._audit(
            record,
            "a2a.task.completed",
            recipient_id,
            f"A2A Task {task_id} 生成回复草稿，已切换为人工审核。",
        )

    def _finish_takeover_stop(
        self,
        record: HumanChatRecord,
        actor_agent_id: str | None = None,
    ) -> None:
        requested_mode = record.requested_mode
        record.stop_requested = False
        record.requested_mode = None
        if requested_mode:
            self._apply_mode(record, requested_mode)
            self._audit(
                record,
                "mode.switched",
                actor_agent_id or record.from_agent_id,
                f"Agent 停止后切换为 {requested_mode.value}。",
            )
            return
        record.state = HumanChatState.STOPPED
        self._audit(
            record,
            "takeover.stopped",
            actor_agent_id or record.from_agent_id,
            "Agent 接管已停止，可继续托管或切换人工沟通。",
        )

    async def _send_to_agent(
        self,
        record: HumanChatRecord,
        recipient_id: str,
        payload: dict[str, object],
    ) -> tuple[str, str, dict[str, object]]:
        if record.topology == HumanChatTopology.RELAY_A_B:
            if self.relay_hub is None:
                raise RuntimeError("Relay is not available")
            return await self.relay_hub.dispatch(recipient_id, payload)
        return await self.communicator.send_json_to_url(
            self.agent_url(record, recipient_id),
            payload,
        )

    @staticmethod
    def _normalized_message(message: str) -> str:
        return "".join(re.findall(r"[\w\u4e00-\u9fff]", message.lower()))

    def _is_repeated_reply(self, record: HumanChatRecord, reply: str) -> bool:
        normalized = self._normalized_message(reply)
        if len(normalized) < 12:
            return False
        previous = [
            self._normalized_message(message.text)
            for message in record.messages[-6:]
            if message.source != HumanChatMessageSource.HUMAN_DIRECT
        ]
        return any(
            SequenceMatcher(None, normalized, candidate).ratio() >= 0.72
            for candidate in previous
            if candidate
        )

    @staticmethod
    def _audit(
        record: HumanChatRecord,
        action: str,
        actor_agent_id: str,
        detail: str,
    ) -> None:
        record.audit.append(
            HumanChatAuditEvent(
                sequence=len(record.audit) + 1,
                action=action,
                actor_agent_id=actor_agent_id,
                detail=detail,
            )
        )

    @staticmethod
    def _sse(event: str, data: HumanChatView) -> str:
        payload = data.model_dump_json(by_alias=True)
        return f"event: {event}\ndata: {payload}\n\n"
