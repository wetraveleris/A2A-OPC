from __future__ import annotations

import os
import json

from uuid import uuid4

from .conversation import A2ACommunicator
from .models import (
    CreateInternetA2ARequest,
    InternetA2ARecord,
    InternetA2ATarget,
)
from .profiles import get_profile


def internet_targets() -> dict[str, InternetA2ATarget]:
    targets: dict[str, InternetA2ATarget] = {}
    remote_agent_b_url = os.getenv("OPC_REMOTE_AGENT_B_URL", "").strip().rstrip("/")
    if remote_agent_b_url:
        targets["computer-b"] = InternetA2ATarget(
            id="computer-b",
            name="电脑 B · 沈知野 Agent",
            base_url=remote_agent_b_url,
            protocol_version="1.0",
            skill_id="employee_chat",
            skill_name="Employee Chat",
            summary="第二台电脑上的独立 Agent。请求经公网 A2A 送达后，由 B 电脑本地模型生成回复。",
            default_prompt="你是谁？请只介绍你自己的身份、所在城市和正在做的项目。",
        )
    targets.update({
        "perkoon": InternetA2ATarget(
            id="perkoon",
            name="Perkoon Agent",
            base_url="https://perkoon.com",
            protocol_version="0.3.0",
            skill_id="describe",
            skill_name="Describe Capabilities",
            summary="互联网上公开运行的 P2P 文件传输 Agent，无需 API Key。",
            default_prompt="请介绍你是谁、能做什么，以及我的个人网站 Agent 下一步怎么与你协作。",
        ),
        "aurelius": InternetA2ATarget(
            id="aurelius",
            name="Aurelius Agent",
            base_url=os.getenv(
                "OPC_AURELIUS_AGENT_URL",
                "https://aureliusagent.dev",
            ).rstrip("/"),
            protocol_version="0.3.0",
            skill_id="strategic-planning",
            skill_name="Strategic Planning",
            summary="互联网上公开运行的战略规划 Agent，无需 API Key。",
            default_prompt=(
                "请为个人网站 Agent 与公网 Agent 的首次协作，给出三个简洁、可执行的下一步。"
            ),
        )
    })
    return targets


class InternetA2AService:
    def __init__(self, communicator: A2ACommunicator) -> None:
        self.communicator = communicator

    def list_targets(self) -> list[InternetA2ATarget]:
        return list(internet_targets().values())

    async def send(self, request: CreateInternetA2ARequest) -> InternetA2ARecord:
        target = internet_targets().get(request.target_id)
        if target is None:
            raise KeyError(f"Unknown internet A2A target: {request.target_id}")

        prompt = request.prompt.strip()
        if not prompt:
            raise ValueError("prompt must include a non-empty request")
        if target.id == "perkoon":
            sent_message = (
                "OPC Link personal website Agent is discovering public A2A agents. "
                f"Please answer as the Perkoon Agent. User request: {prompt}"
            )
            result = await self.communicator.send_text_to_url(
                target.base_url,
                sent_message,
            )
            return InternetA2ARecord(
                id=str(uuid4()),
                target_id=target.id,
                target_name=target.name,
                target_url=target.base_url,
                skill_id=target.skill_id,
                skill_name=target.skill_name,
                prompt=prompt,
                sent_message=sent_message,
                task_id=result.task_id,
                task_state=result.task_state,
                response_text=result.text,
            )

        if target.id == "computer-b":
            payload = {
                "protocol": "opc.employee_chat.v1",
                "conversationId": str(uuid4()),
                "turn": 1,
                "senderAgentId": "opc-builder",
                "recipientAgentId": "shen-zhiye",
                "message": prompt,
                "conversationTopic": "回答电脑 A 用户发来的问题",
                "sharedContext": {
                    "goal": "回答电脑 A 用户发来的问题",
                    "knownFacts": [],
                    "decisions": [],
                    "openQuestions": [prompt],
                },
                "privateContextPolicy": "PUBLIC_PROFILE_ONLY",
                "ownerCommitmentAllowed": False,
            }
            task_id, task_state, response = await self.communicator.send_json_to_url(
                target.base_url,
                payload,
            )
            response_text = (
                response.get("reply")
                or response.get("answer")
                or response.get("shortMessage")
                or "远端 OPC Agent 已返回结构化结果。"
            )
            decision_engine = response.get("debug", {}).get("decisionEngine", {})
            return InternetA2ARecord(
                id=str(uuid4()),
                target_id=target.id,
                target_name=target.name,
                target_url=target.base_url,
                skill_id=target.skill_id,
                skill_name=target.skill_name,
                prompt=prompt,
                sent_message=json.dumps(payload, ensure_ascii=False),
                task_id=task_id,
                task_state=task_state,
                response_text=str(response_text),
                remote_provider=str(decision_engine.get("provider", "")) or None,
                remote_model=str(decision_engine.get("model", "")) or None,
            )

        if target.skill_id == "public_inquiry":
            source = get_profile("opc-builder")
            payload = {
                "protocol": "opc.public_inquiry.v1",
                "conversationId": str(uuid4()),
                "senderAgentId": "opc-builder",
                "recipientAgentId": "shen-zhiye",
                "intent": target.skill_id,
                "question": prompt,
                "disclosedProfile": source.a2a_packet(),
                "humanConfirmationRequired": False,
            }
            task_id, task_state, response = await self.communicator.send_json_to_url(
                target.base_url,
                payload,
            )
            response_text = (
                response.get("answer")
                or response.get("shortMessage")
                or response.get("summary")
                or "远端 OPC Agent 已返回结构化结果。"
            )
            return InternetA2ARecord(
                id=str(uuid4()),
                target_id=target.id,
                target_name=target.name,
                target_url=target.base_url,
                skill_id=target.skill_id,
                skill_name=target.skill_name,
                prompt=prompt,
                sent_message=json.dumps(payload, ensure_ascii=False),
                task_id=task_id,
                task_state=task_state,
                response_text=str(response_text),
                remote_provider=str(
                    response.get("decisionEngine", {}).get("provider", "")
                ) or None,
                remote_model=str(
                    response.get("decisionEngine", {}).get("model", "")
                ) or None,
            )

        sent_message = (
            "You are being contacted over the A2A protocol by Chen Mo's personal "
            "OPC Agent at OPC Link. The owner has approved this request. "
            f"User request: {prompt}"
        )
        result = await self.communicator.send_text_to_url(
            target.base_url,
            sent_message,
        )
        return InternetA2ARecord(
            id=str(uuid4()),
            target_id=target.id,
            target_name=target.name,
            target_url=target.base_url,
            skill_id=target.skill_id,
            skill_name=target.skill_name,
            prompt=prompt,
            sent_message=sent_message,
            task_id=result.task_id,
            task_state=result.task_state,
            response_text=result.text,
        )
