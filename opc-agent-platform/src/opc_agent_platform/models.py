from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel


class APIModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
    )


class AIUsage(APIModel):
    monthly_token_range: str
    monthly_budget_cny: str
    preferred_models: list[str]


class AgentProfile(APIModel):
    id: str
    name: str
    role: str
    city: str
    project_summary: str
    project_directions: list[str]
    offers: list[str]
    needs: list[str]
    career_highlights: list[str]
    mbti: str
    future_goals: list[str]
    collaboration_style: str
    availability_hours_per_week: int
    ai_usage: AIUsage
    direction_codes: list[str] = Field(exclude=True)
    capability_codes: list[str] = Field(exclude=True)
    need_codes: list[str] = Field(exclude=True)
    value_codes: list[str] = Field(exclude=True)
    collaboration_risks: list[str] = Field(exclude=True)
    open_questions: list[str] = Field(exclude=True)
    private_contact: dict[str, str] = Field(exclude=True, repr=False)

    def public_view(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True)

    def a2a_packet(self) -> dict[str, Any]:
        packet = self.public_view()
        packet.update(
            {
                "directionCodes": list(self.direction_codes),
                "capabilityCodes": list(self.capability_codes),
                "needCodes": list(self.need_codes),
                "valueCodes": list(self.value_codes),
            }
        )
        return packet


class Recommendation(StrEnum):
    WORTH_MEETING = "WORTH_MEETING"
    KEEP_EXPLORING = "KEEP_EXPLORING"
    LOW_FIT = "LOW_FIT"


class ModelUsage(APIModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    calls: int = 0


class MatchReport(APIModel):
    recommendation: Recommendation
    confidence: Literal["HIGH", "MEDIUM_HIGH", "MEDIUM"]
    score: int = Field(ge=0, le=100)
    summary: str
    common_ground: list[str]
    complementarity: list[str]
    risks: list[str]
    unconfirmed: list[str]
    evidence_task_ids: list[str] = Field(default_factory=list)
    generated_by: Literal["rules", "deepseek"] = "rules"
    model: str | None = None
    token_usage: ModelUsage | None = None


class ScreeningState(StrEnum):
    CREATED = "CREATED"
    SCREENING = "SCREENING"
    WAITING_REMOTE_AGENT = "WAITING_REMOTE_AGENT"
    REPORT_GENERATED = "REPORT_GENERATED"
    WAITING_OWNER_APPROVAL = "WAITING_OWNER_APPROVAL"
    WAITING_REMOTE_APPROVAL = "WAITING_REMOTE_APPROVAL"
    MUTUAL_APPROVED = "MUTUAL_APPROVED"
    HUMAN_CONNECTED = "HUMAN_CONNECTED"
    DECLINED = "DECLINED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


class TranscriptTurn(APIModel):
    round: int = Field(ge=1, le=3)
    from_agent_id: str
    to_agent_id: str
    task_id: str
    task_state: str
    request: dict[str, Any]
    response: dict[str, Any]
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class ScreeningRecord(APIModel):
    id: str
    from_agent_id: str
    to_agent_id: str
    state: ScreeningState
    report: MatchReport | None = None
    transcript: list[TranscriptTurn] = Field(default_factory=list)
    approvals: list[str] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class CreateScreeningRequest(APIModel):
    from_agent_id: str
    to_agent_id: str


class DecisionRequest(APIModel):
    agent_id: str
    decision: Literal["approve", "decline"]


class AvailabilityStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    BUSY = "BUSY"
    OUTSIDE_WORKING_HOURS = "OUTSIDE_WORKING_HOURS"


class ScheduleState(StrEnum):
    CREATED = "CREATED"
    CHECKING_TARGET = "CHECKING_TARGET"
    CHECKING_REQUESTER = "CHECKING_REQUESTER"
    AGENTS_CONFIRMED = "AGENTS_CONFIRMED"
    WAITING_HUMAN_CONFIRMATION = "WAITING_HUMAN_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    NO_COMMON_SLOT = "NO_COMMON_SLOT"
    DECLINED = "DECLINED"
    FAILED = "FAILED"


class CreateScheduleInquiryRequest(APIModel):
    from_agent_id: str
    to_agent_id: str
    requested_start: datetime
    duration_minutes: int = Field(default=30, ge=15, le=120)
    topic: str = Field(min_length=1, max_length=120)

    @field_validator("requested_start")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("requestedStart must include a timezone")
        return value


class ScheduleRecord(APIModel):
    id: str
    from_agent_id: str
    to_agent_id: str
    requested_start: datetime
    duration_minutes: int
    timezone: str = "Asia/Shanghai"
    topic: str
    state: ScheduleState
    agents_confirmed: bool = False
    human_confirmation_required: bool = True
    confirmations: list[str] = Field(default_factory=list)
    transcript: list[TranscriptTurn] = Field(default_factory=list)
    message: str = ""
    alternatives: list[datetime] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class ScheduleConfirmationRequest(APIModel):
    agent_id: str
    decision: Literal["confirm", "decline"]


class LiveConversationState(StrEnum):
    CREATED = "CREATED"
    STREAMING = "STREAMING"
    NO_COMMON_SLOT = "NO_COMMON_SLOT"
    WAITING_HUMAN_CONFIRMATION = "WAITING_HUMAN_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    DECLINED = "DECLINED"
    FAILED = "FAILED"


class CreateLiveScheduleRequest(APIModel):
    from_agent_id: str
    to_agent_id: str
    requested_start: datetime
    duration_minutes: int = Field(default=30, ge=15, le=120)
    topic: str = Field(min_length=1, max_length=120)

    @field_validator("requested_start")
    @classmethod
    def require_live_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("requestedStart must include a timezone")
        return value


class LiveConversationMessage(APIModel):
    turn: int
    speaker_agent_id: str
    recipient_agent_id: str
    text: str
    task_id: str
    task_state: str
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class LiveConversationRecord(APIModel):
    id: str
    from_agent_id: str
    to_agent_id: str
    requested_start: datetime
    duration_minutes: int
    topic: str
    state: LiveConversationState
    messages: list[LiveConversationMessage] = Field(default_factory=list)
    agents_confirmed: bool = False
    human_confirmation_required: bool = True
    confirmations: list[str] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
