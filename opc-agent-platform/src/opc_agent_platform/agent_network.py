from __future__ import annotations

from datetime import datetime

from pydantic import Field
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from .account_models import FriendRequestCreate, PublicUserView
from .account_service import AccountService
from .conversation import ScreeningService
from .database import (
    AgentIntroduction,
    Connection,
    Database,
    Device,
    FriendRequest,
    User,
    UserProfile,
    utc_now,
)
from .models import AIUsage, APIModel, AgentProfile
from .profiles import PROFILES, get_profile
from .relay import RelayHub


class AgentDeviceClaimRequest(APIModel):
    agent_id: str = Field(min_length=1, max_length=100)
    name: str = Field(default="本机 Agent", min_length=1, max_length=100)
    platform: str = Field(default="desktop", min_length=1, max_length=50)


class AgentDeviceView(APIModel):
    id: str
    agent_id: str
    name: str
    platform: str
    online: bool
    provider: str | None = None
    model: str | None = None
    is_mine: bool = False
    is_claimed: bool = False


class OnlineAgentCardView(APIModel):
    agent_id: str
    agent_name: str
    name: str
    role: str
    city: str
    project_summary: str
    offers: list[str]
    needs: list[str]
    collaboration_style: str
    online: bool
    provider: str | None = None
    model: str | None = None
    owner: PublicUserView
    relation_state: str = "NONE"


class AgentIntroductionCreate(APIModel):
    target_agent_id: str = Field(min_length=1, max_length=100)
    goal: str = Field(
        default="请双方介绍自己、了解能力与需求，并判断是否值得建立联系。",
        min_length=1,
        max_length=500,
    )


class AgentIntroductionView(APIModel):
    id: str
    screening_id: str
    source_agent_id: str
    target_agent_id: str
    source_name: str
    target_name: str
    source_agent_name: str
    target_agent_name: str
    goal: str
    state: str
    relation_state: str
    report: dict[str, object]
    transcript: list[dict[str, object]]
    friend_request_id: str | None
    created_at: datetime


class AgentNetworkService:
    def __init__(
        self,
        database: Database,
        relay_hub: RelayHub,
        screening_service: ScreeningService,
        account_service: AccountService,
    ) -> None:
        self.database = database
        self.relay_hub = relay_hub
        self.screening_service = screening_service
        self.account_service = account_service

    @staticmethod
    def _public_user(user: User) -> PublicUserView:
        return PublicUserView(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
        )

    def _relay_status(self, agent_id: str) -> dict[str, object]:
        return self.relay_hub.status([agent_id])[0]

    @staticmethod
    def _owner_agent_profile(
        user: User,
        profile: UserProfile,
        agent_id: str,
    ) -> AgentProfile:
        template = get_profile(agent_id)
        return template.model_copy(
            update={
                "name": user.display_name,
                "role": profile.role.strip() or "OPC 创作者",
                "city": profile.city.strip() or "线上",
                "project_summary": profile.project_summary.strip()
                or "正在通过个人 Agent 认识新的合作伙伴。",
                "project_directions": [],
                "offers": list(profile.offers) or ["开放介绍与经验交流"],
                "needs": list(profile.needs) or ["寻找合适的连接与合作机会"],
                "career_highlights": [],
                "mbti": "",
                "future_goals": [],
                "collaboration_style": profile.collaboration_style.strip()
                or "先相互了解，再由双方本人决定是否继续。",
                "availability_hours_per_week": 0,
                "ai_usage": AIUsage(
                    monthly_token_range="未公开",
                    monthly_budget_cny="未公开",
                    preferred_models=[],
                ),
                "direction_codes": [],
                "capability_codes": [],
                "need_codes": [],
                "value_codes": [],
                "collaboration_risks": [],
                "open_questions": ["具体合作目标与边界"],
                "private_contact": {},
            }
        )

    def list_device_options(self, user_id: str) -> list[AgentDeviceView]:
        with self.database.session() as session:
            claimed = {
                device.agent_id: device
                for device in session.scalars(
                    select(Device).where(Device.agent_id.is_not(None))
                ).all()
                if device.agent_id
            }
            views: list[AgentDeviceView] = []
            for agent_id in PROFILES:
                status = self._relay_status(agent_id)
                metadata = status.get("metadata", {})
                metadata = metadata if isinstance(metadata, dict) else {}
                device = claimed.get(agent_id)
                views.append(
                    AgentDeviceView(
                        id=device.id if device else agent_id,
                        agent_id=agent_id,
                        name=device.name if device else f"{get_profile(agent_id).name}的 Agent",
                        platform=device.platform if device else "desktop",
                        online=bool(status.get("online")),
                        provider=str(metadata.get("provider")) if metadata.get("provider") else None,
                        model=str(metadata.get("model")) if metadata.get("model") else None,
                        is_mine=bool(device and device.user_id == user_id),
                        is_claimed=device is not None,
                    )
                )
            return views

    def claim_device(
        self,
        user_id: str,
        request: AgentDeviceClaimRequest,
    ) -> AgentDeviceView:
        agent_id = request.agent_id.strip()
        if agent_id not in PROFILES:
            raise KeyError("Agent not found")
        status = self._relay_status(agent_id)
        if not status.get("online"):
            raise ValueError("Only an online Agent can be bound")
        metadata = status.get("metadata", {})
        metadata = metadata if isinstance(metadata, dict) else {}
        with self.database.session() as session:
            existing = session.scalar(select(Device).where(Device.agent_id == agent_id))
            if existing and existing.user_id != user_id:
                raise ValueError("This Agent is already bound to another account")
            if existing is None:
                existing = Device(
                    user_id=user_id,
                    agent_id=agent_id,
                    name=request.name.strip(),
                    platform=request.platform.strip(),
                )
                session.add(existing)
            else:
                existing.name = request.name.strip()
                existing.platform = request.platform.strip()
            existing.provider = str(metadata.get("provider") or "") or None
            existing.model = str(metadata.get("model") or "") or None
            existing.status = "ONLINE"
            existing.last_seen_at = utc_now()
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise ValueError("This Agent is already bound") from exc
            session.refresh(existing)
            return AgentDeviceView(
                id=existing.id,
                agent_id=agent_id,
                name=existing.name,
                platform=existing.platform,
                online=True,
                provider=existing.provider,
                model=existing.model,
                is_mine=True,
                is_claimed=True,
            )

    @staticmethod
    def _relation_state(session, viewer_id: str, target_id: str) -> str:
        pair = sorted([viewer_id, target_id])
        connected = session.scalar(
            select(Connection).where(
                Connection.user_a_id == pair[0],
                Connection.user_b_id == pair[1],
            )
        )
        if connected:
            return "CONNECTED"
        pending = session.scalar(
            select(FriendRequest).where(
                FriendRequest.status == "PENDING",
                or_(
                    (FriendRequest.sender_user_id == viewer_id)
                    & (FriendRequest.recipient_user_id == target_id),
                    (FriendRequest.sender_user_id == target_id)
                    & (FriendRequest.recipient_user_id == viewer_id),
                ),
            )
        )
        if pending is None:
            return "NONE"
        return (
            "PENDING_OUTGOING"
            if pending.sender_user_id == viewer_id
            else "PENDING_INCOMING"
        )

    def list_online_agents(self, user_id: str) -> list[OnlineAgentCardView]:
        with self.database.session() as session:
            devices = session.scalars(
                select(Device).where(Device.agent_id.is_not(None))
            ).all()
            cards: list[OnlineAgentCardView] = []
            for device in devices:
                if not device.agent_id or device.user_id == user_id:
                    continue
                if device.agent_id not in PROFILES or not self.relay_hub.is_online(device.agent_id):
                    continue
                owner = session.get(User, device.user_id)
                if owner is None or owner.status != "ACTIVE":
                    continue
                owner_profile = session.get(UserProfile, device.user_id)
                if owner_profile is None or not owner_profile.discoverable:
                    continue
                status = self._relay_status(device.agent_id)
                metadata = status.get("metadata", {})
                metadata = metadata if isinstance(metadata, dict) else {}
                cards.append(
                    OnlineAgentCardView(
                        agent_id=device.agent_id,
                        agent_name=device.name,
                        name=owner.display_name,
                        role=owner_profile.role.strip() or "OPC 创作者",
                        city=owner_profile.city.strip() or "线上",
                        project_summary=owner_profile.project_summary.strip()
                        or "正在通过个人 Agent 认识新的合作伙伴。",
                        offers=list(owner_profile.offers),
                        needs=list(owner_profile.needs),
                        collaboration_style=owner_profile.collaboration_style.strip()
                        or "先相互了解，再由双方本人决定是否继续。",
                        online=True,
                        provider=str(metadata.get("provider")) if metadata.get("provider") else None,
                        model=str(metadata.get("model")) if metadata.get("model") else None,
                        owner=self._public_user(owner),
                        relation_state=self._relation_state(session, user_id, owner.id),
                    )
                )
            return cards

    def _source_device(self, session, user_id: str) -> Device:
        devices = session.scalars(
            select(Device).where(
                Device.user_id == user_id,
                Device.agent_id.is_not(None),
            )
        ).all()
        for device in devices:
            if device.agent_id and self.relay_hub.is_online(device.agent_id):
                return device
        raise ValueError("Bind and start your local Agent first")

    async def create_introduction(
        self,
        user_id: str,
        request: AgentIntroductionCreate,
    ) -> AgentIntroductionView:
        target_agent_id = request.target_agent_id.strip()
        with self.database.session() as session:
            source = self._source_device(session, user_id)
            target = session.scalar(
                select(Device).where(Device.agent_id == target_agent_id)
            )
            if target is None:
                raise KeyError("Target Agent has no bound owner")
            if target.user_id == user_id or source.agent_id == target_agent_id:
                raise ValueError("An Agent cannot introduce itself")
            source_user = session.get(User, user_id)
            target_user = session.get(User, target.user_id)
            source_owner_profile = session.get(UserProfile, user_id)
            target_owner_profile = session.get(UserProfile, target.user_id)
            if not all(
                (source_user, target_user, source_owner_profile, target_owner_profile)
            ):
                raise ValueError("Both Agent owners must have an active profile")
            target_user_id = target.user_id
            source_agent_id = str(source.agent_id)
            source_profile = self._owner_agent_profile(
                source_user,
                source_owner_profile,
                source_agent_id,
            )
            target_profile = self._owner_agent_profile(
                target_user,
                target_owner_profile,
                target_agent_id,
            )

        screening = await self.screening_service.start(
            source_agent_id,
            target_agent_id,
            use_relay=True,
            source_profile=source_profile,
            target_profile=target_profile,
        )
        record = AgentIntroduction(
            initiator_user_id=user_id,
            target_user_id=target_user_id,
            source_agent_id=source_agent_id,
            target_agent_id=target_agent_id,
            screening_id=screening.id,
            goal=request.goal.strip(),
            report=(
                screening.report.model_dump(mode="json", by_alias=True)
                if screening.report
                else {}
            ),
            transcript=[
                turn.model_dump(mode="json", by_alias=True)
                for turn in screening.transcript
            ],
        )
        with self.database.session() as session:
            session.add(record)
            session.commit()
            session.refresh(record)
            return self._introduction_view(session, record, user_id)

    def request_contact(self, user_id: str, introduction_id: str) -> AgentIntroductionView:
        with self.database.session() as session:
            record = session.get(AgentIntroduction, introduction_id)
            if record is None or record.initiator_user_id != user_id:
                raise KeyError("Agent introduction not found")
            target = session.get(User, record.target_user_id)
            if target is None:
                raise KeyError("Target owner not found")
            target_username = target.username

        friend_request = self.account_service.create_friend_request(
            user_id,
            FriendRequestCreate(
                target_username=target_username,
                message="双方 Agent 已通过 A2A 完成初步了解，希望建立联系。",
            ),
        )
        with self.database.session() as session:
            record = session.get(AgentIntroduction, introduction_id)
            if record is None:
                raise KeyError("Agent introduction not found")
            record.friend_request_id = friend_request.id
            record.state = "CONTACT_REQUESTED"
            record.updated_at = utc_now()
            session.commit()
            session.refresh(record)
            return self._introduction_view(session, record, user_id)

    def get_introduction(self, user_id: str, introduction_id: str) -> AgentIntroductionView:
        with self.database.session() as session:
            record = session.get(AgentIntroduction, introduction_id)
            if record is None or user_id not in {
                record.initiator_user_id,
                record.target_user_id,
            }:
                raise KeyError("Agent introduction not found")
            return self._introduction_view(session, record, user_id)

    def _introduction_view(
        self,
        session,
        record: AgentIntroduction,
        viewer_id: str,
    ) -> AgentIntroductionView:
        source_user = session.get(User, record.initiator_user_id)
        target_user = session.get(User, record.target_user_id)
        source_device = session.scalar(
            select(Device).where(Device.agent_id == record.source_agent_id)
        )
        target_device = session.scalar(
            select(Device).where(Device.agent_id == record.target_agent_id)
        )
        return AgentIntroductionView(
            id=record.id,
            screening_id=record.screening_id,
            source_agent_id=record.source_agent_id,
            target_agent_id=record.target_agent_id,
            source_name=(
                source_user.display_name
                if source_user
                else get_profile(record.source_agent_id).name
            ),
            target_name=(
                target_user.display_name
                if target_user
                else get_profile(record.target_agent_id).name
            ),
            source_agent_name=(
                source_device.name if source_device else record.source_agent_id
            ),
            target_agent_name=(
                target_device.name if target_device else record.target_agent_id
            ),
            goal=record.goal,
            state=record.state,
            relation_state=self._relation_state(
                session,
                viewer_id,
                record.target_user_id
                if viewer_id == record.initiator_user_id
                else record.initiator_user_id,
            ),
            report=dict(record.report or {}),
            transcript=list(record.transcript or []),
            friend_request_id=record.friend_request_id,
            created_at=record.created_at,
        )
