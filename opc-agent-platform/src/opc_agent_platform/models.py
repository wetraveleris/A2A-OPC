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
    generated_by: Literal["rules", "deepseek", "ollama"] = "rules"
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


class InternetA2ATarget(APIModel):
    id: str
    name: str
    base_url: str
    protocol_version: str
    skill_id: str
    skill_name: str
    summary: str
    default_prompt: str


class CreateInternetA2ARequest(APIModel):
    target_id: str = "computer-b"
    prompt: str = Field(min_length=1, max_length=500)


class InternetA2ARecord(APIModel):
    id: str
    target_id: str
    target_name: str
    target_url: str
    skill_id: str
    skill_name: str
    prompt: str
    sent_message: str
    task_id: str
    task_state: str
    response_text: str
    remote_provider: str | None = None
    remote_model: str | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class EmployeeChatContext(APIModel):
    goal: str
    known_facts: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class EmployeeChatContextPatch(APIModel):
    known_facts_add: list[str] = Field(default_factory=list)
    decisions_add: list[str] = Field(default_factory=list)
    open_questions_add: list[str] = Field(default_factory=list)
    open_questions_resolved: list[str] = Field(default_factory=list)


class CreateEmployeeChatRequest(APIModel):
    from_agent_id: str = "opc-builder"
    to_agent_id: str = "shen-zhiye"
    goal: str = Field(min_length=1, max_length=500)
    max_turns: int = Field(default=4, ge=2, le=6)


class EmployeeChatTurn(APIModel):
    turn: int
    from_agent_id: str
    to_agent_id: str
    agent_card_url: str
    jsonrpc_url: str
    jsonrpc_method: str = "message/send"
    task_id: str
    task_state: str
    request: dict[str, Any]
    response: dict[str, Any]
    context_before: EmployeeChatContext
    context_after: EmployeeChatContext
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class EmployeeChatRecord(APIModel):
    id: str
    from_agent_id: str
    to_agent_id: str
    goal: str
    state: Literal["COMPLETED", "FAILED"]
    protocol: str = "opc.employee_chat.v1"
    context: EmployeeChatContext
    turns: list[EmployeeChatTurn] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class HumanChatState(StrEnum):
    WAITING_OWNER_A = "WAITING_OWNER_A"
    WAITING_OWNER_B = "WAITING_OWNER_B"
    AGENT_READY = "AGENT_READY"
    AGENT_RUNNING = "AGENT_RUNNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    HUMAN_DIRECT = "HUMAN_DIRECT"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class HumanChatMode(StrEnum):
    HUMAN_APPROVAL = "HUMAN_APPROVAL"
    AGENT_TAKEOVER = "AGENT_TAKEOVER"
    HUMAN_DIRECT = "HUMAN_DIRECT"


class HumanChatRunPolicy(StrEnum):
    CONTINUOUS = "CONTINUOUS"
    LIMITED = "LIMITED"


class HumanChatMessageSource(StrEnum):
    AGENT_AUTO = "AGENT_AUTO"
    AGENT_APPROVED = "AGENT_APPROVED"
    HUMAN_DIRECT = "HUMAN_DIRECT"


class HumanChatTopology(StrEnum):
    LOCAL = "LOCAL"
    PUBLIC_A_B = "PUBLIC_A_B"
    RELAY_A_B = "RELAY_A_B"


class CreateHumanChatRequest(APIModel):
    from_agent_id: str = "opc-builder"
    to_agent_id: str = "shen-zhiye"
    goal: str = Field(min_length=1, max_length=500)
    max_turns: int | None = Field(default=None, ge=1, le=100)
    run_policy: HumanChatRunPolicy = HumanChatRunPolicy.CONTINUOUS
    mode: HumanChatMode = HumanChatMode.HUMAN_APPROVAL
    topology: HumanChatTopology = HumanChatTopology.LOCAL


class HumanChatDraft(APIModel):
    turn: int
    speaker_agent_id: str
    recipient_agent_id: str
    original_text: str
    context_patch: EmployeeChatContextPatch = Field(
        default_factory=EmployeeChatContextPatch
    )
    source_task_id: str | None = None
    source_task_state: str | None = None
    request: dict[str, Any] | None = None
    response: dict[str, Any] | None = None
    already_sent: bool = False


class HumanChatMessage(APIModel):
    turn: int
    speaker_agent_id: str
    recipient_agent_id: str
    text: str
    original_text: str
    human_edited: bool
    human_approved: bool = True
    source: HumanChatMessageSource = HumanChatMessageSource.AGENT_APPROVED
    approved_by_agent_id: str
    source_task_id: str | None = None
    source_task_state: str | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class HumanChatAuditEvent(APIModel):
    sequence: int
    action: str
    actor_agent_id: str
    detail: str
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class HumanChatRecord(APIModel):
    id: str
    from_agent_id: str
    to_agent_id: str
    goal: str
    max_turns: int | None = None
    run_policy: HumanChatRunPolicy = HumanChatRunPolicy.CONTINUOUS
    mode: HumanChatMode = HumanChatMode.HUMAN_APPROVAL
    topology: HumanChatTopology = HumanChatTopology.LOCAL
    agent_urls: dict[str, str] = Field(default_factory=dict)
    state: HumanChatState
    context: EmployeeChatContext
    pending_draft: HumanChatDraft | None = None
    messages: list[HumanChatMessage] = Field(default_factory=list)
    a2a_turns: list[EmployeeChatTurn] = Field(default_factory=list)
    audit: list[HumanChatAuditEvent] = Field(default_factory=list)
    access_tokens: dict[str, str] = Field(exclude=True, repr=False)
    version: int = 1
    stop_requested: bool = Field(default=False, exclude=True)
    requested_mode: HumanChatMode | None = Field(default=None, exclude=True)
    pause_reason: str | None = None
    error: str | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class HumanChatParticipant(APIModel):
    side: Literal["a", "b"]
    agent_id: str
    agent_name: str
    role: str
    computer_name: str
    agent_url: str


class HumanChatView(APIModel):
    id: str
    goal: str
    state: HumanChatState
    max_turns: int | None = None
    run_policy: HumanChatRunPolicy
    mode: HumanChatMode
    topology: HumanChatTopology
    version: int
    viewer: HumanChatParticipant
    other: HumanChatParticipant
    waiting_for_agent_id: str | None = None
    can_act: bool = False
    can_start: bool = False
    can_stop: bool = False
    can_send_direct: bool = False
    can_switch_to_approval: bool = False
    pause_reason: str | None = None
    context: EmployeeChatContext
    pending_draft: HumanChatDraft | None = None
    messages: list[HumanChatMessage] = Field(default_factory=list)
    a2a_turns: list[EmployeeChatTurn] = Field(default_factory=list)
    audit: list[HumanChatAuditEvent] = Field(default_factory=list)
    error: str | None = None


class HumanChatCreated(APIModel):
    id: str
    mode: HumanChatMode
    state: HumanChatState
    topology: HumanChatTopology
    agent_a_url: str
    agent_b_url: str
    participant_a_url: str
    participant_b_url: str


class HumanChatApprovalRequest(APIModel):
    message: str = Field(min_length=1, max_length=500)
    expected_version: int = Field(ge=1)


class HumanChatRejectionRequest(APIModel):
    reason: str = Field(default="对方暂不继续这次 Agent 沟通", max_length=200)
    expected_version: int = Field(ge=1)


class HumanChatStopRequest(APIModel):
    reason: str = Field(default="本人停止 Agent 自动接管", max_length=200)


class HumanChatStartRequest(APIModel):
    reason: str = Field(default="本人授权 Agent 自动接管", max_length=200)


class HumanChatSwitchModeRequest(APIModel):
    mode: HumanChatMode
    reason: str = Field(default="用户切换沟通控制模式", max_length=200)


class HumanChatDirectMessageRequest(APIModel):
    message: str = Field(min_length=1, max_length=1000)
