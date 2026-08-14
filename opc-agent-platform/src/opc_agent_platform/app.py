from __future__ import annotations

import os

from pathlib import Path

import httpx
import uvicorn

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .a2a_runtime import mount_a2a_agents
from .conversation import A2ACommunicator, ScreeningService
from .deepseek import DeepSeekClient
from .employee_chat import EmployeeChatService, EmployeeChatStore
from .human_chat import HumanChatService, HumanChatStore
from .internet_a2a import InternetA2AService
from .live_conversation import LiveConversationService, LiveConversationStore
from .models import (
    AgentProfile,
    CreateInternetA2ARequest,
    CreateEmployeeChatRequest,
    EmployeeChatRecord,
    CreateHumanChatRequest,
    HumanChatApprovalRequest,
    HumanChatCreated,
    HumanChatDirectMessageRequest,
    HumanChatRejectionRequest,
    HumanChatStartRequest,
    HumanChatStopRequest,
    HumanChatSwitchModeRequest,
    HumanChatView,
    CreateLiveScheduleRequest,
    CreateScreeningRequest,
    CreateScheduleInquiryRequest,
    DecisionRequest,
    InternetA2ARecord,
    InternetA2ATarget,
    LiveConversationRecord,
    ScheduleConfirmationRequest,
    ScheduleRecord,
    ScreeningRecord,
)
from .profiles import PROFILES, get_profile
from .relay import RelayError, RelayHub
from .scheduling import ScheduleService, ScheduleStore
from .store import ScreeningStore


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def create_app(
    base_url: str | None = None,
    a2a_transport: httpx.AsyncBaseTransport | None = None,
    use_environment_llm: bool = True,
    deepseek_client: DeepSeekClient | None = None,
) -> FastAPI:
    resolved_base_url = (
        base_url
        or os.getenv("OPC_PUBLIC_BASE_URL")
        or "http://127.0.0.1:8010"
    ).rstrip("/")
    app = FastAPI(
        title="OPC Agent Platform",
        version="0.1.0",
        description="Three-round, auditable OPC Agent screening over A2A 1.0.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:8010",
            "http://localhost:8010",
            "http://127.0.0.1:8088",
        ],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    resolved_deepseek_client = deepseek_client or (
        DeepSeekClient.from_environment() if use_environment_llm else None
    )
    store = ScreeningStore()
    communicator = A2ACommunicator(
        base_url=resolved_base_url,
        transport=a2a_transport,
    )
    screening_service = ScreeningService(
        store=store,
        communicator=communicator,
        deepseek_client=resolved_deepseek_client,
    )
    schedule_store = ScheduleStore()
    schedule_service = ScheduleService(
        store=schedule_store,
        communicator=communicator,
    )
    live_conversation_store = LiveConversationStore()
    live_conversation_service = LiveConversationService(
        store=live_conversation_store,
        communicator=communicator,
    )
    internet_a2a_service = InternetA2AService(communicator=communicator)
    employee_chat_store = EmployeeChatStore()
    employee_chat_service = EmployeeChatService(
        store=employee_chat_store,
        communicator=communicator,
    )
    human_chat_store = HumanChatStore()
    relay_hub = RelayHub(token=os.getenv("OPC_RELAY_TOKEN", ""))
    human_chat_service = HumanChatService(
        store=human_chat_store,
        communicator=communicator,
        relay_hub=relay_hub,
    )
    app.state.screening_store = store
    app.state.screening_service = screening_service
    app.state.schedule_store = schedule_store
    app.state.schedule_service = schedule_service
    app.state.live_conversation_store = live_conversation_store
    app.state.live_conversation_service = live_conversation_service
    app.state.internet_a2a_service = internet_a2a_service
    app.state.employee_chat_store = employee_chat_store
    app.state.employee_chat_service = employee_chat_service
    app.state.human_chat_store = human_chat_store
    app.state.human_chat_service = human_chat_service
    app.state.relay_hub = relay_hub

    @app.get("/api/relay/agents")
    async def relay_agents() -> list[dict[str, object]]:
        return relay_hub.status(list(PROFILES))

    @app.websocket("/api/relay/ws/{agent_id}")
    async def relay_websocket(websocket: WebSocket, agent_id: str) -> None:
        if agent_id not in PROFILES:
            await websocket.close(code=4404, reason="Unknown Agent")
            return
        if not relay_hub.authorized(websocket.headers.get("authorization")):
            await websocket.close(code=4401, reason="Unauthorized")
            return
        await relay_hub.connect(agent_id, websocket)
        try:
            while True:
                message = await websocket.receive_json()
                await relay_hub.receive(agent_id, message)
        except WebSocketDisconnect:
            pass
        except RelayError as exc:
            await websocket.close(code=4400, reason=str(exc)[:120])
        finally:
            await relay_hub.disconnect(agent_id, websocket)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {
            "status": "ok",
            "protocol": "A2A 1.0",
            "runtime": "multi-tenant",
            "decisionEngine": (
                resolved_deepseek_client.provider
                if resolved_deepseek_client
                else "rules"
            ),
            "model": (
                resolved_deepseek_client.model
                if resolved_deepseek_client
                else "none"
            ),
        }

    @app.get("/api/agents", response_model=list[AgentProfile])
    async def list_agents() -> list[AgentProfile]:
        return list(PROFILES.values())

    @app.get("/api/agents/{agent_id}", response_model=AgentProfile)
    async def agent_detail(agent_id: str) -> AgentProfile:
        try:
            return get_profile(agent_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/internet-a2a/targets", response_model=list[InternetA2ATarget])
    async def list_internet_a2a_targets() -> list[InternetA2ATarget]:
        return internet_a2a_service.list_targets()

    @app.post(
        "/api/internet-a2a/demo",
        response_model=InternetA2ARecord,
        status_code=201,
    )
    async def create_internet_a2a_demo(
        request: CreateInternetA2ARequest,
    ) -> InternetA2ARecord:
        try:
            return await internet_a2a_service.send(request)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Internet A2A demo failed: {exc}",
            ) from exc

    @app.post(
        "/api/employee-chats",
        response_model=EmployeeChatRecord,
        status_code=201,
    )
    async def create_employee_chat(
        request: CreateEmployeeChatRequest,
    ) -> EmployeeChatRecord:
        try:
            return await employee_chat_service.create(request)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Employee A2A chat failed: {exc}",
            ) from exc

    @app.get(
        "/api/employee-chats/{conversation_id}",
        response_model=EmployeeChatRecord,
    )
    async def get_employee_chat(conversation_id: str) -> EmployeeChatRecord:
        try:
            return await employee_chat_store.get(conversation_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/api/human-agent-chats",
        response_model=HumanChatCreated,
        status_code=201,
    )
    async def create_human_agent_chat(
        request: CreateHumanChatRequest,
    ) -> HumanChatCreated:
        try:
            return await human_chat_service.create(request)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get(
        "/api/human-agent-chats/{conversation_id}",
        response_model=HumanChatView,
    )
    async def get_human_agent_chat(
        conversation_id: str,
        token: str,
    ) -> HumanChatView:
        try:
            return human_chat_service.view(conversation_id, token)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.get("/api/human-agent-chats/{conversation_id}/events")
    async def stream_human_agent_chat(
        conversation_id: str,
        token: str,
    ) -> StreamingResponse:
        try:
            human_chat_service.view(conversation_id, token)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return StreamingResponse(
            human_chat_service.stream(conversation_id, token),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post(
        "/api/human-agent-chats/{conversation_id}/approve",
        response_model=HumanChatView,
    )
    async def approve_human_agent_draft(
        conversation_id: str,
        token: str,
        request: HumanChatApprovalRequest,
    ) -> HumanChatView:
        try:
            return await human_chat_service.approve(conversation_id, token, request)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Human-in-the-loop A2A turn failed: {exc}",
            ) from exc

    @app.post(
        "/api/human-agent-chats/{conversation_id}/reject",
        response_model=HumanChatView,
    )
    async def reject_human_agent_draft(
        conversation_id: str,
        token: str,
        request: HumanChatRejectionRequest,
    ) -> HumanChatView:
        try:
            return await human_chat_service.reject(conversation_id, token, request)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/api/human-agent-chats/{conversation_id}/mode",
        response_model=HumanChatView,
    )
    async def switch_human_agent_chat_mode(
        conversation_id: str,
        token: str,
        request: HumanChatSwitchModeRequest,
    ) -> HumanChatView:
        try:
            return await human_chat_service.switch_mode(conversation_id, token, request)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Mode switch failed: {exc}",
            ) from exc

    @app.post(
        "/api/human-agent-chats/{conversation_id}/messages",
        response_model=HumanChatView,
    )
    async def send_human_chat_message(
        conversation_id: str,
        token: str,
        request: HumanChatDirectMessageRequest,
    ) -> HumanChatView:
        try:
            return await human_chat_service.send_direct_message(
                conversation_id,
                token,
                request,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post(
        "/api/human-agent-chats/{conversation_id}/start",
        response_model=HumanChatView,
    )
    async def start_human_agent_takeover(
        conversation_id: str,
        token: str,
        request: HumanChatStartRequest,
    ) -> HumanChatView:
        try:
            return await human_chat_service.start(conversation_id, token, request)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post(
        "/api/human-agent-chats/{conversation_id}/stop",
        response_model=HumanChatView,
    )
    async def stop_human_agent_takeover(
        conversation_id: str,
        token: str,
        request: HumanChatStopRequest,
    ) -> HumanChatView:
        try:
            return await human_chat_service.stop(conversation_id, token, request)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/screenings", response_model=ScreeningRecord, status_code=201)
    async def create_screening(
        request: CreateScreeningRequest,
    ) -> ScreeningRecord:
        try:
            return await screening_service.start(
                request.from_agent_id,
                request.to_agent_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"A2A screening failed: {exc}",
            ) from exc

    @app.get("/api/screenings/{screening_id}", response_model=ScreeningRecord)
    async def get_screening(screening_id: str) -> ScreeningRecord:
        try:
            return await store.get(screening_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/screenings/{screening_id}/transcript")
    async def get_transcript(screening_id: str) -> dict[str, object]:
        try:
            screening = await store.get(screening_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "screeningId": screening.id,
            "rounds": [turn.model_dump(by_alias=True) for turn in screening.transcript],
        }

    @app.post(
        "/api/screenings/{screening_id}/approve",
        response_model=ScreeningRecord,
    )
    async def decide_screening(
        screening_id: str,
        request: DecisionRequest,
    ) -> ScreeningRecord:
        try:
            return await store.decide(
                screening_id,
                request.agent_id,
                request.decision,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post(
        "/api/schedule-inquiries",
        response_model=ScheduleRecord,
        status_code=201,
    )
    async def create_schedule_inquiry(
        request: CreateScheduleInquiryRequest,
    ) -> ScheduleRecord:
        try:
            return await schedule_service.start(request)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"A2A scheduling failed: {exc}",
            ) from exc

    @app.get(
        "/api/schedule-inquiries/{schedule_id}",
        response_model=ScheduleRecord,
    )
    async def get_schedule_inquiry(schedule_id: str) -> ScheduleRecord:
        try:
            return await schedule_store.get(schedule_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/api/schedule-inquiries/{schedule_id}/confirm",
        response_model=ScheduleRecord,
    )
    async def confirm_schedule_inquiry(
        schedule_id: str,
        request: ScheduleConfirmationRequest,
    ) -> ScheduleRecord:
        try:
            return await schedule_store.confirm(
                schedule_id,
                request.agent_id,
                request.decision,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/live-conversations/schedule", status_code=201)
    async def create_live_schedule_conversation(
        request: CreateLiveScheduleRequest,
    ) -> StreamingResponse:
        try:
            record = await live_conversation_service.create(request)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return StreamingResponse(
            live_conversation_service.stream(record.id),
            status_code=201,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get(
        "/api/live-conversations/{conversation_id}",
        response_model=LiveConversationRecord,
    )
    async def get_live_conversation(
        conversation_id: str,
    ) -> LiveConversationRecord:
        try:
            return await live_conversation_store.get(conversation_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/api/live-conversations/{conversation_id}/confirm",
        response_model=LiveConversationRecord,
    )
    async def confirm_live_conversation(
        conversation_id: str,
        request: ScheduleConfirmationRequest,
    ) -> LiveConversationRecord:
        try:
            return await live_conversation_store.confirm(
                conversation_id,
                request.agent_id,
                request.decision,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    mount_a2a_agents(
        app,
        PROFILES,
        resolved_base_url,
        deepseek_client=resolved_deepseek_client,
    )

    workspace_root = Path(__file__).resolve().parents[3]
    video_dir = workspace_root / "视频"
    prototype_dir = workspace_root / "opc-link-prototype"
    if video_dir.is_dir():
        app.mount("/视频", StaticFiles(directory=video_dir), name="videos")
    if prototype_dir.is_dir():
        app.mount("/app", StaticFiles(directory=prototype_dir, html=True), name="prototype")

        @app.get("/", include_in_schema=False)
        async def root() -> RedirectResponse:
            return RedirectResponse(url="/app/")

    return app


app = create_app()


def run() -> None:
    uvicorn.run(
        "opc_agent_platform.app:app",
        host="127.0.0.1",
        port=8010,
        reload=False,
    )
