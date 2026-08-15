from __future__ import annotations

import hashlib
import secrets

from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from .account_models import (
    ConnectionView,
    DiscoveryProfileView,
    FriendRequestCreate,
    FriendRequestView,
    LoginRequest,
    ProfileUpdateRequest,
    ProfileView,
    RegisterRequest,
    UserView,
    WorkCreateRequest,
    WorkUpdateRequest,
    WorkView,
)
from .database import (
    Connection,
    Database,
    Device,
    FriendRequest,
    User,
    UserProfile,
    UserSession,
    Work,
    utc_now,
)


SESSION_TTL = timedelta(days=30)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class AccountService:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.passwords = PasswordHasher(
            time_cost=3,
            memory_cost=65536,
            parallelism=2,
        )

    @staticmethod
    def _user_view(user: User) -> UserView:
        return UserView(
            id=user.id,
            username=user.username,
            email=user.email,
            display_name=user.display_name,
            status=user.status,
            created_at=user.created_at,
        )

    @classmethod
    def _profile_view(cls, user: User, profile: UserProfile) -> ProfileView:
        return ProfileView(
            user=cls._user_view(user),
            role=profile.role,
            city=profile.city,
            bio=profile.bio,
            project_summary=profile.project_summary,
            offers=list(profile.offers or []),
            needs=list(profile.needs or []),
            collaboration_style=profile.collaboration_style,
            languages=list(profile.languages or []),
            avatar_url=profile.avatar_url,
            intro_video_url=profile.intro_video_url,
            discoverable=profile.discoverable,
            updated_at=profile.updated_at,
        )

    @staticmethod
    def _work_view(work: Work) -> WorkView:
        return WorkView(
            id=work.id,
            title=work.title,
            summary=work.summary,
            role=work.role,
            status=work.status,
            visibility=work.visibility,
            cover_url=work.cover_url,
            video_url=work.video_url,
            links=list(work.links or []),
            skills=list(work.skills or []),
            sort_order=work.sort_order,
            created_at=work.created_at,
            updated_at=work.updated_at,
        )

    def _issue_session(self, session, user_id: str) -> str:
        token = secrets.token_urlsafe(40)
        session.add(
            UserSession(
                user_id=user_id,
                token_hash=_token_hash(token),
                expires_at=utc_now() + SESSION_TTL,
            )
        )
        return token

    def register(self, request: RegisterRequest) -> tuple[ProfileView, str]:
        with self.database.session() as session:
            existing = session.scalar(
                select(User).where(
                    or_(
                        User.username == request.username,
                        User.email == request.email,
                    )
                )
            )
            if existing:
                raise ValueError("Username or email is already registered")
            user = User(
                username=request.username,
                email=request.email,
                password_hash=self.passwords.hash(request.password),
                display_name=request.display_name.strip(),
            )
            session.add(user)
            session.flush()
            profile = UserProfile(user_id=user.id)
            session.add(profile)
            token = self._issue_session(session, user.id)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise ValueError("Username or email is already registered") from exc
            session.refresh(user)
            session.refresh(profile)
            return self._profile_view(user, profile), token

    def login(self, request: LoginRequest) -> tuple[ProfileView, str]:
        identity = request.identity.strip().lower()
        with self.database.session() as session:
            user = session.scalar(
                select(User).where(
                    or_(User.username == identity, User.email == identity)
                )
            )
            if user is None or user.status != "ACTIVE":
                raise PermissionError("Invalid username/email or password")
            try:
                self.passwords.verify(user.password_hash, request.password)
            except (VerifyMismatchError, InvalidHashError) as exc:
                raise PermissionError("Invalid username/email or password") from exc
            if self.passwords.check_needs_rehash(user.password_hash):
                user.password_hash = self.passwords.hash(request.password)
            profile = session.get(UserProfile, user.id)
            if profile is None:
                profile = UserProfile(user_id=user.id)
                session.add(profile)
            token = self._issue_session(session, user.id)
            session.commit()
            session.refresh(profile)
            return self._profile_view(user, profile), token

    def authenticate(self, token: str | None) -> User:
        if not token:
            raise PermissionError("Authentication required")
        with self.database.session() as session:
            record = session.scalar(
                select(UserSession).where(
                    UserSession.token_hash == _token_hash(token),
                    UserSession.revoked_at.is_(None),
                )
            )
            if record is None or _aware(record.expires_at) <= utc_now():
                raise PermissionError("Session expired")
            user = session.get(User, record.user_id)
            if user is None or user.status != "ACTIVE":
                raise PermissionError("Account is unavailable")
            session.expunge(user)
            return user

    def logout(self, token: str | None) -> None:
        if not token:
            return
        with self.database.session() as session:
            record = session.scalar(
                select(UserSession).where(
                    UserSession.token_hash == _token_hash(token)
                )
            )
            if record:
                record.revoked_at = utc_now()
                session.commit()

    def get_profile(self, user_id: str) -> ProfileView:
        with self.database.session() as session:
            user = session.get(User, user_id)
            profile = session.get(UserProfile, user_id)
            if user is None or profile is None:
                raise KeyError("Account profile not found")
            return self._profile_view(user, profile)

    def update_profile(
        self,
        user_id: str,
        request: ProfileUpdateRequest,
    ) -> ProfileView:
        with self.database.session() as session:
            user = session.get(User, user_id)
            profile = session.get(UserProfile, user_id)
            if user is None or profile is None:
                raise KeyError("Account profile not found")
            changes = request.model_dump(exclude_unset=True, by_alias=False)
            display_name = changes.pop("display_name", None)
            if display_name is not None:
                user.display_name = display_name.strip()
            for field, value in changes.items():
                if field in {"offers", "needs", "languages"} and value is not None:
                    value = [str(item).strip() for item in value if str(item).strip()]
                setattr(profile, field, value)
            profile.updated_at = utc_now()
            session.commit()
            session.refresh(user)
            session.refresh(profile)
            return self._profile_view(user, profile)

    def list_works(self, user_id: str) -> list[WorkView]:
        with self.database.session() as session:
            works = session.scalars(
                select(Work)
                .where(Work.user_id == user_id)
                .order_by(Work.sort_order, Work.created_at.desc())
            ).all()
            return [self._work_view(work) for work in works]

    def list_discovery(
        self,
        limit: int = 12,
        offset: int = 0,
        exclude_user_id: str | None = None,
    ) -> list[DiscoveryProfileView]:
        limit = min(max(limit, 1), 30)
        offset = max(offset, 0)
        with self.database.session() as session:
            profiles = session.scalars(
                select(UserProfile)
                .join(User, User.id == UserProfile.user_id)
                .where(
                    User.status == "ACTIVE",
                    UserProfile.discoverable.is_(True),
                    *([User.id != exclude_user_id] if exclude_user_id else []),
                )
                .order_by(UserProfile.updated_at.desc())
                .offset(offset)
                .limit(limit)
            ).all()
            views: list[DiscoveryProfileView] = []
            for profile in profiles:
                user = session.get(User, profile.user_id)
                if user is None:
                    continue
                works = session.scalars(
                    select(Work)
                    .where(
                        Work.user_id == user.id,
                        Work.visibility == "PUBLIC",
                    )
                    .order_by(Work.sort_order, Work.created_at.desc())
                    .limit(12)
                ).all()
                views.append(
                    DiscoveryProfileView(
                        profile_id=user.id,
                        username=user.username,
                        display_name=user.display_name,
                        role=profile.role,
                        city=profile.city,
                        bio=profile.bio,
                        project_summary=profile.project_summary,
                        offers=list(profile.offers or []),
                        needs=list(profile.needs or []),
                        collaboration_style=profile.collaboration_style,
                        languages=list(profile.languages or []),
                        avatar_url=profile.avatar_url,
                        intro_video_url=profile.intro_video_url,
                        works=[self._work_view(work) for work in works],
                    )
                )
            return views

    def create_work(self, user_id: str, request: WorkCreateRequest) -> WorkView:
        with self.database.session() as session:
            work = Work(user_id=user_id, **request.model_dump(by_alias=False))
            session.add(work)
            session.commit()
            session.refresh(work)
            return self._work_view(work)

    def update_work(
        self,
        user_id: str,
        work_id: str,
        request: WorkUpdateRequest,
    ) -> WorkView:
        with self.database.session() as session:
            work = session.get(Work, work_id)
            if work is None or work.user_id != user_id:
                raise KeyError("Work not found")
            for field, value in request.model_dump(
                exclude_unset=True,
                by_alias=False,
            ).items():
                setattr(work, field, value)
            work.updated_at = utc_now()
            session.commit()
            session.refresh(work)
            return self._work_view(work)

    def delete_work(self, user_id: str, work_id: str) -> None:
        with self.database.session() as session:
            work = session.get(Work, work_id)
            if work is None or work.user_id != user_id:
                raise KeyError("Work not found")
            session.delete(work)
            session.commit()

    def create_friend_request(
        self,
        user_id: str,
        request: FriendRequestCreate,
    ) -> FriendRequestView:
        with self.database.session() as session:
            target = session.scalar(
                select(User).where(
                    User.username == request.target_username.strip().lower()
                )
            )
            sender = session.get(User, user_id)
            if target is None or sender is None:
                raise KeyError("Target user not found")
            if target.id == user_id:
                raise ValueError("You cannot connect with yourself")
            pair = sorted([user_id, target.id])
            connected = session.scalar(
                select(Connection).where(
                    Connection.user_a_id == pair[0],
                    Connection.user_b_id == pair[1],
                )
            )
            if connected:
                raise ValueError("You are already connected")
            pending = session.scalar(
                select(FriendRequest).where(
                    FriendRequest.status == "PENDING",
                    or_(
                        (
                            (FriendRequest.sender_user_id == user_id)
                            & (FriendRequest.recipient_user_id == target.id)
                        ),
                        (
                            (FriendRequest.sender_user_id == target.id)
                            & (FriendRequest.recipient_user_id == user_id)
                        ),
                    ),
                )
            )
            if pending:
                raise ValueError("A connection request is already pending")
            record = FriendRequest(
                sender_user_id=user_id,
                recipient_user_id=target.id,
                message=request.message.strip(),
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return FriendRequestView(
                id=record.id,
                direction="OUTGOING",
                user=self._user_view(target),
                status=record.status,
                message=record.message,
                created_at=record.created_at,
            )

    def list_friend_requests(self, user_id: str) -> list[FriendRequestView]:
        with self.database.session() as session:
            records = session.scalars(
                select(FriendRequest)
                .where(
                    or_(
                        FriendRequest.sender_user_id == user_id,
                        FriendRequest.recipient_user_id == user_id,
                    )
                )
                .order_by(FriendRequest.created_at.desc())
            ).all()
            views: list[FriendRequestView] = []
            for record in records:
                incoming = record.recipient_user_id == user_id
                other_id = (
                    record.sender_user_id if incoming else record.recipient_user_id
                )
                other = session.get(User, other_id)
                if other:
                    views.append(
                        FriendRequestView(
                            id=record.id,
                            direction="INCOMING" if incoming else "OUTGOING",
                            user=self._user_view(other),
                            status=record.status,
                            message=record.message,
                            created_at=record.created_at,
                        )
                    )
            return views

    def decide_friend_request(
        self,
        user_id: str,
        request_id: str,
        accept: bool,
    ) -> None:
        with self.database.session() as session:
            record = session.get(FriendRequest, request_id)
            if record is None or record.recipient_user_id != user_id:
                raise KeyError("Incoming connection request not found")
            if record.status != "PENDING":
                raise ValueError("Connection request has already been decided")
            record.status = "ACCEPTED" if accept else "DECLINED"
            record.responded_at = utc_now()
            if accept:
                pair = sorted([record.sender_user_id, record.recipient_user_id])
                session.add(Connection(user_a_id=pair[0], user_b_id=pair[1]))
            session.commit()

    def list_connections(self, user_id: str) -> list[ConnectionView]:
        with self.database.session() as session:
            records = session.scalars(
                select(Connection)
                .where(
                    or_(
                        Connection.user_a_id == user_id,
                        Connection.user_b_id == user_id,
                    )
                )
                .order_by(Connection.created_at.desc())
            ).all()
            views: list[ConnectionView] = []
            for record in records:
                other_id = (
                    record.user_b_id
                    if record.user_a_id == user_id
                    else record.user_a_id
                )
                other = session.get(User, other_id)
                if other is None:
                    continue
                devices = session.scalars(
                    select(Device).where(Device.user_id == other_id)
                ).all()
                views.append(
                    ConnectionView(
                        id=record.id,
                        user=self._user_view(other),
                        devices=[
                            {
                                "id": device.id,
                                "name": device.name,
                                "platform": device.platform,
                                "agentId": device.agent_id,
                                "provider": device.provider,
                                "model": device.model,
                                "status": device.status,
                                "lastSeenAt": (
                                    device.last_seen_at.isoformat()
                                    if device.last_seen_at
                                    else None
                                ),
                            }
                            for device in devices
                        ],
                        created_at=record.created_at,
                    )
                )
            return views
