from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from .account_models import (
    ConnectionChatRoomCreateRequest,
    ConnectionView,
    DiscoveryAssessmentView,
    DiscoveryProfileView,
    FriendRequestCreate,
    FriendRequestView,
    LoginRequest,
    ProfileUpdateRequest,
    ProfileView,
    RegisterRequest,
    WorkCreateRequest,
    WorkUpdateRequest,
    WorkView,
)
from .account_service import AccountService, SESSION_TTL
from .models import CreateHumanChatRequest, HumanChatCreated, HumanChatTopology


SESSION_COOKIE = "opc_session"
router = APIRouter(prefix="/api")


def _service(request: Request) -> AccountService:
    return request.app.state.account_service


def _secure_cookie(request: Request) -> bool:
    forwarded = request.headers.get("x-forwarded-proto", "")
    return request.url.scheme == "https" or forwarded.split(",", 1)[0] == "https"


def _set_session_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        secure=_secure_cookie(request),
        samesite="lax",
        path="/",
    )


def _current_user(request: Request):
    try:
        return _service(request).authenticate(request.cookies.get(SESSION_COOKIE))
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.get("/discovery/feed", response_model=list[DiscoveryProfileView])
def discovery_feed(
    request: Request,
    limit: int = Query(default=12, ge=1, le=30),
    offset: int = Query(default=0, ge=0),
) -> list[DiscoveryProfileView]:
    viewer_user_id = None
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        try:
            viewer_user_id = _service(request).authenticate(token).id
        except PermissionError:
            pass
    return _service(request).list_discovery(
        limit=limit,
        offset=offset,
        viewer_user_id=viewer_user_id,
    )


@router.post(
    "/discovery/{profile_id}/assessment",
    response_model=DiscoveryAssessmentView,
)
def assess_discovery_profile(
    profile_id: str,
    request: Request,
) -> DiscoveryAssessmentView:
    user = _current_user(request)
    try:
        return _service(request).assess_discovery_profile(user.id, profile_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/auth/register", response_model=ProfileView, status_code=201)
def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
) -> ProfileView:
    try:
        profile, token = _service(request).register(payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _set_session_cookie(request, response, token)
    return profile


@router.post("/auth/login", response_model=ProfileView)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
) -> ProfileView:
    try:
        profile, token = _service(request).login(payload)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    _set_session_cookie(request, response, token)
    return profile


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response) -> Response:
    _service(request).logout(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/auth/me", response_model=ProfileView)
def auth_me(request: Request) -> ProfileView:
    user = _current_user(request)
    return _service(request).get_profile(user.id)


@router.get("/me/profile", response_model=ProfileView)
def get_my_profile(request: Request) -> ProfileView:
    user = _current_user(request)
    return _service(request).get_profile(user.id)


@router.put("/me/profile", response_model=ProfileView)
def update_my_profile(
    payload: ProfileUpdateRequest,
    request: Request,
) -> ProfileView:
    user = _current_user(request)
    try:
        return _service(request).update_profile(user.id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/me/works", response_model=list[WorkView])
def list_my_works(request: Request) -> list[WorkView]:
    user = _current_user(request)
    return _service(request).list_works(user.id)


@router.post("/me/works", response_model=WorkView, status_code=201)
def create_my_work(
    payload: WorkCreateRequest,
    request: Request,
) -> WorkView:
    user = _current_user(request)
    return _service(request).create_work(user.id, payload)


@router.put("/me/works/{work_id}", response_model=WorkView)
def update_my_work(
    work_id: str,
    payload: WorkUpdateRequest,
    request: Request,
) -> WorkView:
    user = _current_user(request)
    try:
        return _service(request).update_work(user.id, work_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/me/works/{work_id}", status_code=204)
def delete_my_work(work_id: str, request: Request, response: Response) -> Response:
    user = _current_user(request)
    try:
        _service(request).delete_work(user.id, work_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/connection-requests", response_model=list[FriendRequestView])
def list_connection_requests(request: Request) -> list[FriendRequestView]:
    user = _current_user(request)
    return _service(request).list_friend_requests(user.id)


@router.post(
    "/connection-requests",
    response_model=FriendRequestView,
    status_code=201,
)
def create_connection_request(
    payload: FriendRequestCreate,
    request: Request,
) -> FriendRequestView:
    user = _current_user(request)
    try:
        return _service(request).create_friend_request(user.id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/connection-requests/{request_id}/accept", status_code=204)
def accept_connection_request(
    request_id: str,
    request: Request,
    response: Response,
) -> Response:
    user = _current_user(request)
    try:
        _service(request).decide_friend_request(user.id, request_id, True)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/connection-requests/{request_id}/decline", status_code=204)
def decline_connection_request(
    request_id: str,
    request: Request,
    response: Response,
) -> Response:
    user = _current_user(request)
    try:
        _service(request).decide_friend_request(user.id, request_id, False)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/connections", response_model=list[ConnectionView])
def list_connections(request: Request) -> list[ConnectionView]:
    user = _current_user(request)
    connections = _service(request).list_connections(user.id)
    relay_hub = request.app.state.relay_hub
    for connection in connections:
        for device in connection.devices:
            agent_id = str(device.get("agentId") or "")
            device["status"] = (
                "ONLINE" if agent_id and relay_hub.is_online(agent_id) else "OFFLINE"
            )
    return connections


@router.post(
    "/connections/{connection_id}/chat-rooms",
    response_model=HumanChatCreated,
    status_code=201,
)
async def create_connection_chat_room(
    connection_id: str,
    payload: ConnectionChatRoomCreateRequest,
    request: Request,
) -> HumanChatCreated:
    user = _current_user(request)
    try:
        setup = _service(request).get_connection_chat_setup(user.id, connection_id)
        context = setup["initial_context"].model_copy(update={"goal": payload.goal.strip()})
        chat_request = CreateHumanChatRequest(
            from_agent_id=str(setup["source_agent_id"]),
            to_agent_id=str(setup["target_agent_id"]),
            goal=payload.goal.strip(),
            run_policy=payload.run_policy,
            mode=payload.mode,
            topology=HumanChatTopology.RELAY_A_B,
            connection_id=connection_id,
            initial_context=context,
            source_profile=setup["source_profile"],
            target_profile=setup["target_profile"],
            source_runtime=setup["source_runtime"],
            target_runtime=setup["target_runtime"],
        )
        return await request.app.state.human_chat_service.create(chat_request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put(
    "/connections/{connection_id}/chat-room",
    response_model=HumanChatCreated,
)
async def get_or_create_connection_chat_room(
    connection_id: str,
    payload: ConnectionChatRoomCreateRequest,
    request: Request,
) -> HumanChatCreated:
    return await create_connection_chat_room(connection_id, payload, request)
