from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .agent_network import (
    AgentDeviceClaimRequest,
    AgentDeviceView,
    AgentIntroductionCreate,
    AgentIntroductionView,
    OnlineAgentCardView,
)


router = APIRouter(prefix="/api", tags=["agent-network"])


def _user(request: Request):
    try:
        return request.app.state.account_service.authenticate(
            request.cookies.get("opc_session")
        )
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _service(request: Request):
    return request.app.state.agent_network_service


@router.get("/me/agent-devices", response_model=list[AgentDeviceView])
def list_my_agent_device_options(request: Request) -> list[AgentDeviceView]:
    user = _user(request)
    return _service(request).list_device_options(user.id)


@router.post("/me/agent-devices/claim", response_model=AgentDeviceView)
def claim_my_agent_device(
    payload: AgentDeviceClaimRequest,
    request: Request,
) -> AgentDeviceView:
    user = _user(request)
    try:
        return _service(request).claim_device(user.id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

@router.get("/discovery/online-agents", response_model=list[OnlineAgentCardView])
def list_online_agent_cards(request: Request) -> list[OnlineAgentCardView]:
    user = _user(request)
    return _service(request).list_online_agents(user.id)


@router.post(
    "/agent-introductions",
    response_model=AgentIntroductionView,
    status_code=201,
)
async def create_agent_introduction(
    payload: AgentIntroductionCreate,
    request: Request,
) -> AgentIntroductionView:
    user = _user(request)
    try:
        return await _service(request).start_introduction(user.id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"A2A introduction failed: {exc}") from exc


@router.get(
    "/agent-introductions",
    response_model=list[AgentIntroductionView],
)
def list_agent_introductions(request: Request) -> list[AgentIntroductionView]:
    user = _user(request)
    return _service(request).list_introductions(user.id)


@router.get(
    "/agent-introductions/{introduction_id}",
    response_model=AgentIntroductionView,
)
def get_agent_introduction(
    introduction_id: str,
    request: Request,
) -> AgentIntroductionView:
    user = _user(request)
    try:
        return _service(request).get_introduction(user.id, introduction_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/agent-introductions/{introduction_id}/request-contact",
    response_model=AgentIntroductionView,
)
def request_agent_contact(
    introduction_id: str,
    request: Request,
) -> AgentIntroductionView:
    user = _user(request)
    try:
        return _service(request).request_contact(user.id, introduction_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/agent-introductions/{introduction_id}/dismiss",
    response_model=AgentIntroductionView,
)
def dismiss_agent_introduction(
    introduction_id: str,
    request: Request,
) -> AgentIntroductionView:
    user = _user(request)
    try:
        return _service(request).dismiss_introduction(user.id, introduction_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
