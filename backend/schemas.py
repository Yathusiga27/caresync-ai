from datetime import datetime
from typing import Literal, Optional

from pydantic import Field, StrictBool, ValidationInfo, field_validator

from backend.models.supervision import (
    AvailabilitySource,
    AvailabilityStatus,
    CareSyncModel,
    MemberRole,
)


class FamilyMemberCreate(CareSyncModel):
    name: str = Field(min_length=1)
    role: MemberRole

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("name must not be blank")
        return name


class CaregiverSchedulingUpdate(CareSyncModel):
    actor_id: str = Field(min_length=1)
    available_for_scheduling: StrictBool


class ContractCreate(CareSyncModel):
    family_id: str
    child_id: str
    caregiver_id: str
    guardian_id: Optional[str] = None
    activity: str = Field(min_length=1)
    expected_location: str = Field(min_length=1)
    start_time: datetime
    end_time: datetime

    @field_validator("start_time", "end_time")
    @classmethod
    def timestamps_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value

    @field_validator("end_time")
    @classmethod
    def end_must_follow_start(cls, value: datetime, info: ValidationInfo) -> datetime:
        start_time = info.data.get("start_time")
        if start_time is not None and value <= start_time:
            raise ValueError("end_time must be after start_time")
        return value


class CaregiverAvailabilityCreate(CareSyncModel):
    start_time: datetime
    end_time: datetime
    availability_status: AvailabilityStatus
    source: AvailabilitySource

    @field_validator("start_time", "end_time")
    @classmethod
    def timestamps_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value

    @field_validator("end_time")
    @classmethod
    def end_must_follow_start(cls, value: datetime, info: ValidationInfo) -> datetime:
        start_time = info.data.get("start_time")
        if start_time is not None and value <= start_time:
            raise ValueError("end_time must be after start_time")
        return value


class ApprovalDecisionRequest(CareSyncModel):
    actor_id: str = Field(min_length=1)


class ScheduleDecisionRequest(ApprovalDecisionRequest):
    decision: Literal["ACCEPTED", "DECLINED"]
    reason: Optional[str] = Field(default=None, max_length=500)
    expected_version: Optional[int] = Field(default=None, ge=1)


class ScheduleCaregiverRequest(ApprovalDecisionRequest):
    caregiver_id: str = Field(min_length=1)
    expected_version: int = Field(ge=1)


class ChatHistoryMessage(CareSyncModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class AgentChatContext(CareSyncModel):
    contract_id: Optional[str] = Field(default=None, min_length=1, max_length=200)


class AgentChatRequest(CareSyncModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[ChatHistoryMessage] = Field(default_factory=list, max_length=6)
    context: Optional[AgentChatContext] = None

    @field_validator("message")
    @classmethod
    def message_must_contain_non_whitespace(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message must not be blank")
        return value


class AgentChatResponse(CareSyncModel):
    reply: str
    model: str
    proposal: Optional["AgentReplacementSuggestion"] = None
    proposal_validation_error: Optional[str] = None


class AgentReplacementSuggestion(CareSyncModel):
    contract_id: str
    original_contract_version: int = Field(ge=1)
    proposed_caregiver_id: str
    reason: str = Field(min_length=1)


class AgentModelAnswer(CareSyncModel):
    reply: str = Field(min_length=1)
    proposal: Optional[AgentReplacementSuggestion] = None
