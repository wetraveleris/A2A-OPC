from __future__ import annotations

import logging
from typing import Any

from a2a.helpers import (
    get_data_parts,
    new_data_part,
    new_task_from_user_message,
    new_text_message,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState

from .deepseek import DeepSeekClient
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

logger = logging.getLogger(__name__)


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

        if request.get("protocol") == "opc.public_inquiry.v1":
            return self._respond_to_public_inquiry(request)
        if request.get("protocol") == "opc.employee_chat.v1":
            return await self._respond_to_employee_chat(request)

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

    def _respond_to_public_inquiry(self, request: dict[str, Any]) -> dict[str, Any]:
        question = str(request.get("question", "")).strip()
        normalized = question.lower()
        if any(marker in normalized for marker in ("你是谁", "who are you", "介绍", "identity")):
            answer = (
                f"我是{self.profile.name}的 OPC Agent，代表他公开介绍项目、能力和协作边界；"
                f"他是{self.profile.city}的{self.profile.role}，正在做：{self.profile.project_summary}"
            )
        elif any(marker in normalized for marker in ("能做什么", "能力", "提供", "offer")):
            answer = f"{self.profile.name}可以提供：" + "、".join(self.profile.offers) + "。"
        elif any(marker in normalized for marker in ("需要", "寻找", "need")):
            answer = f"{self.profile.name}正在寻找：" + "、".join(self.profile.needs) + "。"
        else:
            answer = (
                f"{self.profile.name}的公开项目是：{self.profile.project_summary}"
                f"协作方式是：{self.profile.collaboration_style}"
            )

        return {
            "protocol": "opc.public_inquiry.v1",
            "agentId": self.profile.id,
            "agentName": self.profile.name,
            "question": question,
            "answer": answer,
            "publicProfile": self.profile.public_view(),
            "humanConfirmationRequired": False,
            "decisionEngine": {"provider": "rules", "model": None},
        }

    async def _respond_to_employee_chat(
        self,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        sender_id = str(request.get("senderAgentId", ""))
        recipient_id = str(request.get("recipientAgentId", ""))
        turn = int(request.get("turn", 0))
        if recipient_id != self.profile.id:
            raise ValueError("Message recipient does not match this Agent")
        if sender_id not in self.profiles or sender_id == self.profile.id:
            raise ValueError("Unknown or invalid sending Agent")
        if turn < 1:
            raise ValueError("Unsupported employee chat turn")
        message = str(request.get("message", "")).strip()
        if not message:
            raise ValueError("Employee chat message must not be empty")
        context = request.get("sharedContext")
        if not isinstance(context, dict):
            raise ValueError("Employee chat requires sharedContext")

        sender = self.profiles[sender_id]
        goal = str(context.get("goal", "这次合作"))
        facts = [str(item) for item in context.get("knownFacts", [])]
        decisions = [str(item) for item in context.get("decisions", [])]
        questions = [str(item) for item in context.get("openQuestions", [])]
        if self.deepseek_client:
            decision, usage = await self.deepseek_client.generate_employee_chat(
                receiver=self.profile,
                sender=sender,
                request=request,
            )
            reply = decision.reply
            action = decision.action
            patch = {}
            decision_engine = {
                "provider": self.deepseek_client.provider,
                "model": self.deepseek_client.model,
                "usage": usage.model_dump(by_alias=True),
            }
        elif turn == 1:
            reply = (
                f"你好，我是{self.profile.name}的 Agent。围绕“{goal}”，"
                f"我能提供{self.profile.offers[0]}和{self.profile.offers[1]}，"
                f"也想确认你这边更看重哪一个具体结果。"
            )
            patch = {
                "knownFactsAdd": [
                    f"{self.profile.name}可提供：{self.profile.offers[0]}、{self.profile.offers[1]}"
                ],
                "openQuestionsAdd": ["这次合作优先验证哪个具体结果"],
            }
            decision_engine = {"provider": "rules", "model": None}
            action = "REPLY"
        elif turn == 2:
            reply = (
                f"我理解了。{self.profile.name}这边更适合先把{goal}拆成一个小实验，"
                f"由我负责{self.profile.offers[0]}，再用真实反馈判断是否继续。"
            )
            patch = {
                "decisionsAdd": ["先用一个小实验验证合作价值"],
                "openQuestionsResolved": ["这次合作优先验证哪个具体结果"],
                "openQuestionsAdd": ["双方如何分工并在两周内验收"],
            }
            decision_engine = {"provider": "rules", "model": None}
            action = "REPLY"
        elif turn == 3:
            reply = (
                f"这个分工可以。我这边建议把验收标准写成一个可观察结果，"
                f"比如完成一次真实用户验证；如果你认可，我们就进入两周试合作。"
            )
            patch = {
                "knownFactsAdd": ["双方都接受先小范围验证，再决定长期合作"],
                "decisionsAdd": ["验收以一次真实用户验证为准"],
                "openQuestionsResolved": ["双方如何分工并在两周内验收"],
                "openQuestionsAdd": ["两周试合作的具体开始时间"],
            }
            decision_engine = {"provider": "rules", "model": None}
            action = "REPLY"
        else:
            reply = (
                f"我同意先按两周试合作推进。下一步由{sender.name}确认业务目标，"
                f"我整理第一版执行清单；涉及承诺、联系方式或合同的部分，仍交给双方本人确认。"
            )
            patch = {
                "decisionsAdd": ["先进行两周试合作，双方本人再确认正式承诺"],
                "openQuestionsResolved": ["两周试合作的具体开始时间"],
            }
            decision_engine = {"provider": "rules", "model": None}
            action = "STOP"
            reply = ""

        return {
            "protocol": "opc.employee_chat.v1",
            "conversationId": request.get("conversationId"),
            "turn": turn,
            "speakerAgentId": self.profile.id,
            "recipientAgentId": sender_id,
            "reply": reply,
            "action": action,
            "contextPatch": patch,
            "debug": {
                "decisionEngine": decision_engine,
                "usedSharedContext": {
                    "knownFacts": len(facts),
                    "decisions": len(decisions),
                    "openQuestions": len(questions),
                },
                "policyDecisions": [
                    "privateContextPolicy accepted",
                    "ownerCommitmentAllowed=false",
                ],
            },
        }

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
            logger.debug(
                "A2A payload received: protocol=%r keys=%s",
                request.get("protocol"),
                sorted(request),
            )
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

    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        raise NotImplementedError("Cancellation is not supported")
