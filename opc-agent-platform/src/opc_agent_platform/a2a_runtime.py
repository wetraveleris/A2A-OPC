from __future__ import annotations

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from fastapi import FastAPI
from starlette.applications import Starlette

from .agent_executor import OPCAgentExecutor, OPCDecisionEngine
from .deepseek import DeepSeekClient
from .models import AgentProfile


SKILLS = [
    AgentSkill(
        id="introduce_opc",
        name="Introduce OPC",
        description="Share an owner-approved public OPC profile.",
        input_modes=["application/json"],
        output_modes=["application/json"],
        tags=["opc", "profile", "a2a"],
    ),
    AgentSkill(
        id="evaluate_collaboration",
        name="Evaluate Collaboration",
        description="Compare project direction, capabilities and working style.",
        input_modes=["application/json"],
        output_modes=["application/json"],
        tags=["opc", "matching"],
    ),
    AgentSkill(
        id="answer_screening",
        name="Answer Screening",
        description="Answer bounded collaboration questions using approved fields.",
        input_modes=["application/json"],
        output_modes=["application/json"],
        tags=["opc", "screening"],
    ),
    AgentSkill(
        id="propose_introduction",
        name="Propose Introduction",
        description="Produce a non-binding recommendation for both owners.",
        input_modes=["application/json"],
        output_modes=["application/json"],
        tags=["opc", "introduction"],
    ),
    AgentSkill(
        id="coordinate_schedule",
        name="Coordinate Schedule",
        description="Check private calendars and return only availability and alternatives.",
        input_modes=["application/json"],
        output_modes=["application/json"],
        tags=["opc", "calendar", "scheduling"],
    ),
]


def mount_a2a_agents(
    app: FastAPI,
    profiles: dict[str, AgentProfile],
    base_url: str,
    deepseek_client: DeepSeekClient | None = None,
) -> None:
    for agent_id, profile in profiles.items():
        agent_url = f"{base_url.rstrip('/')}/a2a/{agent_id}/"
        card = AgentCard(
            name=f"{profile.name}的 OPC Agent",
            description=f"代表{profile.name}进行有边界、可审计的初步合作筛选。",
            version="0.1.0",
            default_input_modes=["application/json"],
            default_output_modes=["application/json"],
            capabilities=AgentCapabilities(streaming=True),
            supported_interfaces=[
                AgentInterface(
                    protocol_binding="JSONRPC",
                    protocol_version="1.0",
                    url=agent_url,
                )
            ],
            skills=SKILLS,
        )
        handler = DefaultRequestHandler(
            agent_executor=OPCAgentExecutor(
                OPCDecisionEngine(
                    profile=profile,
                    profiles=profiles,
                    deepseek_client=deepseek_client,
                )
            ),
            task_store=InMemoryTaskStore(),
            agent_card=card,
        )
        agent_app = Starlette(
            routes=[
                *create_agent_card_routes(card),
                *create_jsonrpc_routes(handler, "/"),
            ]
        )
        app.mount(f"/a2a/{agent_id}", agent_app, name=f"a2a-{agent_id}")
