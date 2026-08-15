from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from .models import APIModel


class RegisterRequest(APIModel):
    username: str = Field(min_length=3, max_length=32)
    email: str = Field(min_length=5, max_length=320)
    password: str = Field(min_length=10, max_length=128)
    display_name: str = Field(min_length=1, max_length=80)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized.replace("_", "").replace("-", "").isalnum():
            raise ValueError("用户名只能包含文字、数字、下划线和连字符")
        return normalized

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized.count("@") != 1 or "." not in normalized.split("@", 1)[1]:
            raise ValueError("请输入有效的邮箱地址")
        return normalized


class LoginRequest(APIModel):
    identity: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=128)


class UserView(APIModel):
    id: str
    username: str
    email: str
    display_name: str
    status: str
    created_at: datetime


class PublicUserView(APIModel):
    id: str
    username: str
    display_name: str


class ProfileView(APIModel):
    user: UserView
    role: str
    city: str
    bio: str
    project_summary: str
    offers: list[str]
    needs: list[str]
    collaboration_style: str
    languages: list[str]
    discoverable: bool
    updated_at: datetime


class ProfileUpdateRequest(APIModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    role: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    bio: str | None = Field(default=None, max_length=1000)
    project_summary: str | None = Field(default=None, max_length=500)
    offers: list[str] | None = None
    needs: list[str] | None = None
    collaboration_style: str | None = Field(default=None, max_length=500)
    languages: list[str] | None = None
    discoverable: bool | None = None


class WorkCreateRequest(APIModel):
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(default="", max_length=2000)
    role: str = Field(default="", max_length=100)
    status: Literal["IDEA", "IN_PROGRESS", "SHIPPED", "ARCHIVED"] = "IN_PROGRESS"
    visibility: Literal["PUBLIC", "CONNECTIONS", "PRIVATE"] = "PUBLIC"
    links: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


class WorkUpdateRequest(APIModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    summary: str | None = Field(default=None, max_length=2000)
    role: str | None = Field(default=None, max_length=100)
    status: Literal["IDEA", "IN_PROGRESS", "SHIPPED", "ARCHIVED"] | None = None
    visibility: Literal["PUBLIC", "CONNECTIONS", "PRIVATE"] | None = None
    links: list[str] | None = None
    skills: list[str] | None = None
    sort_order: int | None = Field(default=None, ge=0, le=10000)


class WorkView(APIModel):
    id: str
    title: str
    summary: str
    role: str
    status: str
    visibility: str
    links: list[str]
    skills: list[str]
    sort_order: int
    created_at: datetime
    updated_at: datetime


class DiscoveryProfileView(APIModel):
    profile_id: str
    username: str
    display_name: str
    role: str
    city: str
    bio: str
    project_summary: str
    offers: list[str]
    needs: list[str]
    collaboration_style: str
    languages: list[str]
    works: list[WorkView]
    relation_state: Literal[
        "NONE",
        "PENDING_OUTGOING",
        "PENDING_INCOMING",
        "CONNECTED",
    ] = "NONE"


class DiscoveryAssessmentView(APIModel):
    target_profile_id: str
    target_username: str
    target_display_name: str
    summary: str
    common_ground: list[str]
    complementarity: list[str]
    questions: list[str]
    relation_state: Literal[
        "NONE",
        "PENDING_OUTGOING",
        "PENDING_INCOMING",
        "CONNECTED",
    ]
    can_request: bool
    basis: Literal["PUBLIC_PROFILE"] = "PUBLIC_PROFILE"


class FriendRequestCreate(APIModel):
    target_username: str = Field(min_length=3, max_length=32)
    message: str = Field(default="", max_length=300)


class FriendRequestView(APIModel):
    id: str
    direction: Literal["INCOMING", "OUTGOING"]
    user: PublicUserView
    status: str
    message: str
    introduction: dict[str, object] | None = None
    created_at: datetime


class ConnectionView(APIModel):
    id: str
    user: PublicUserView
    devices: list[dict[str, object]]
    introductions: list[dict[str, object]]
    created_at: datetime
