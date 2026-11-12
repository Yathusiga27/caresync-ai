from datetime import datetime
from typing import Optional

from pydantic import Field, ValidationInfo, field_validator

from backend.models.supervision import (
    AvailabilitySource,
    AvailabilityStatus,
    CareSyncModel,
)


class ContractCreate(CareSyncModel):
    family_id: str
    child_id: str
    caregiver_id: str
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


class AgentChatRequest(CareSyncModel):
    message: str = Field(min_length=1, max_length=4000)

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
