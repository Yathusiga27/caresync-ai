from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CareSyncModel(BaseModel):
    model_config = ConfigDict(use_enum_values=True, validate_assignment=True)


class Family(CareSyncModel):
    id: str
    name: str
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("created_at")
    @classmethod
    def created_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value


class MemberRole(str, Enum):
    GUARDIAN = "GUARDIAN"
    CAREGIVER = "CAREGIVER"
    CHILD = "CHILD"


class MemberStatus(str, Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class FamilyMember(CareSyncModel):
    id: str
    family_id: str
    name: str
    role: MemberRole
    status: MemberStatus = MemberStatus.ACTIVE


class AvailabilityStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


class AvailabilitySource(str, Enum):
    CAREGIVER = "CAREGIVER"
    GUARDIAN = "GUARDIAN"
    CALENDAR = "CALENDAR"
    SIMULATOR = "SIMULATOR"


class CaregiverAvailability(CareSyncModel):
    id: str
    caregiver_id: str
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


class ContractStatus(str, Enum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    CONFIRMED = "CONFIRMED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CONFLICTED = "CONFLICTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    ESCALATED = "ESCALATED"


class DecisionStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"


class HandoverStatus(str, Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"


class SupervisionContract(CareSyncModel):
    id: str
    family_id: str
    child_id: str
    caregiver_id: str
    activity: str
    expected_location: str
    start_time: datetime
    end_time: datetime
    status: ContractStatus = ContractStatus.DRAFT
    approval_status: DecisionStatus = DecisionStatus.PENDING
    acceptance_status: DecisionStatus = DecisionStatus.PENDING
    handover_status: HandoverStatus = HandoverStatus.PENDING
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("start_time", "end_time", "created_at", "updated_at")
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


class ProposalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"


class SupervisionProposal(CareSyncModel):
    id: str
    original_contract_id: str
    original_contract_version: int = Field(ge=1)
    proposed_caregiver_id: str
    proposed_start_time: datetime
    proposed_end_time: datetime
    reason: str
    status: ProposalStatus = ProposalStatus.PENDING
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: Optional[datetime] = None

    @field_validator("proposed_start_time", "proposed_end_time", "created_at", "expires_at")
    @classmethod
    def timestamps_must_be_timezone_aware(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timestamp must include a timezone")
        return value

    @field_validator("proposed_end_time")
    @classmethod
    def end_must_follow_start(cls, value: datetime, info: ValidationInfo) -> datetime:
        start_time = info.data.get("proposed_start_time")
        if start_time is not None and value <= start_time:
            raise ValueError("proposed_end_time must be after proposed_start_time")
        return value


class ApprovalType(str, Enum):
    GUARDIAN = "GUARDIAN"
    CAREGIVER = "CAREGIVER"


class Approval(CareSyncModel):
    id: str
    proposal_id: str
    approver_id: str
    approval_type: ApprovalType
    status: DecisionStatus = DecisionStatus.PENDING
    response_timestamp: Optional[datetime] = None

    @field_validator("response_timestamp")
    @classmethod
    def response_timestamp_must_be_aware(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timestamp must include a timezone")
        return value


class ChildEventType(str, Enum):
    ARRIVAL = "ARRIVAL"
    DEPARTURE = "DEPARTURE"
    CHECK_IN = "CHECK_IN"
    MISSED_CHECK_IN = "MISSED_CHECK_IN"
    CAREGIVER_CANCELLATION = "CAREGIVER_CANCELLATION"


class VerificationStatus(str, Enum):
    UNVERIFIED = "UNVERIFIED"
    CONFIRMED = "CONFIRMED"
    UNCERTAIN = "UNCERTAIN"


class ChildEvent(CareSyncModel):
    id: str
    child_id: str
    family_id: str
    event_type: ChildEventType
    timestamp: datetime
    source: str
    location: Optional[str] = None
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value


class ActorType(str, Enum):
    USER = "USER"
    AGENT = "AGENT"
    SYSTEM = "SYSTEM"


class AuditEvent(CareSyncModel):
    id: str
    family_id: str
    actor_id: str
    actor_type: ActorType
    action: str
    resource_id: str
    timestamp: datetime = Field(default_factory=utc_now)
    correlation_id: str

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value
