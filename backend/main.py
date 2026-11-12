from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI, HTTPException, status

from backend.agent.agent import AgentOutputError, AgentUnavailableError, CareSyncAgent
from backend.models.supervision import (
    ContractStatus,
    Approval,
    CancellationOutcome,
    CaregiverAvailability,
    CaregiverCancellationEvent,
    DecisionStatus,
    FamilyMember,
    MemberRole,
    SupervisionProposal,
    SupervisionContract,
    ProposalDecisionResult,
)
from backend.schemas import (
    ApprovalDecisionRequest,
    AgentChatRequest,
    AgentChatResponse,
    CaregiverAvailabilityCreate,
    ContractCreate,
)
from backend.store import CancellationConflict, WorkflowConflict, WorkflowNotFound, store

app = FastAPI(
    title="CareSync AI",
    description="A family supervision coordination application.",
    version="0.1.0",
)
agent = CareSyncAgent()


@app.get("/")
def read_root() -> dict[str, str]:
    return {
        "name": "CareSync AI",
        "message": "Family supervision coordination API",
    }


@app.get("/health")
def read_health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/families/{family_id}")
def read_family(family_id: str) -> dict[str, object]:
    family = store.get_family(family_id)
    if family is None:
        raise HTTPException(status_code=404, detail="Family not found")
    return family.model_dump(mode="json")


@app.get("/families/{family_id}/members", response_model=list[FamilyMember])
def read_family_members(family_id: str) -> list[FamilyMember]:
    if store.get_family(family_id) is None:
        raise HTTPException(status_code=404, detail="Family not found")
    return store.list_family_members(family_id)


@app.post(
    "/caregivers/{caregiver_id}/availability",
    response_model=CaregiverAvailability,
    status_code=status.HTTP_201_CREATED,
)
def create_caregiver_availability(
    caregiver_id: str,
    payload: CaregiverAvailabilityCreate,
) -> CaregiverAvailability:
    caregiver = store.get_member(caregiver_id)
    if caregiver is None or caregiver.role != MemberRole.CAREGIVER:
        raise HTTPException(status_code=404, detail="Caregiver not found")
    availability = CaregiverAvailability(
        id=f"availability-{uuid4().hex}",
        caregiver_id=caregiver_id,
        **payload.model_dump(),
    )
    store.create_availability(availability)
    return availability


@app.get(
    "/caregivers/{caregiver_id}/availability",
    response_model=list[CaregiverAvailability],
)
def read_caregiver_availability(caregiver_id: str) -> list[CaregiverAvailability]:
    caregiver = store.get_member(caregiver_id)
    if caregiver is None or caregiver.role != MemberRole.CAREGIVER:
        raise HTTPException(status_code=404, detail="Caregiver not found")
    return store.list_caregiver_availability(caregiver_id)


@app.post(
    "/supervision/contracts",
    response_model=SupervisionContract,
    status_code=status.HTTP_201_CREATED,
)
def create_supervision_contract(payload: ContractCreate) -> SupervisionContract:
    if store.get_family(payload.family_id) is None:
        raise HTTPException(status_code=404, detail="Family not found")

    child = store.get_member(payload.child_id)
    caregiver = store.get_member(payload.caregiver_id)
    if child is None or child.family_id != payload.family_id or child.role != MemberRole.CHILD:
        raise HTTPException(status_code=422, detail="child_id is not a child in this family")
    if (
        caregiver is None
        or caregiver.family_id != payload.family_id
        or caregiver.role != MemberRole.CAREGIVER
    ):
        raise HTTPException(
            status_code=422,
            detail="caregiver_id is not a caregiver in this family",
        )

    contract = SupervisionContract(
        id=f"contract-{uuid4().hex}",
        **payload.model_dump(),
        status=ContractStatus.PENDING_APPROVAL,
        approval_status=DecisionStatus.PENDING,
        acceptance_status=DecisionStatus.PENDING,
    )
    store.create_contract(contract)
    return contract


@app.get("/supervision/contracts/{contract_id}", response_model=SupervisionContract)
def read_supervision_contract(contract_id: str) -> SupervisionContract:
    contract = store.get_contract(contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail="Supervision contract not found")
    return contract


@app.post("/supervision/contracts/{contract_id}/cancel", response_model=SupervisionContract)
def cancel_supervision_contract(contract_id: str) -> SupervisionContract:
    contract = store.get_contract(contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail="Supervision contract not found")
    if contract.status in (ContractStatus.COMPLETED, ContractStatus.CANCELLED):
        raise HTTPException(status_code=409, detail="Contract cannot be cancelled in its current state")

    contract.status = ContractStatus.CANCELLED
    contract.version += 1
    contract.updated_at = datetime.now(timezone.utc)
    store.save_contract(contract)
    return contract


@app.get(
    "/supervision/contracts/{contract_id}/proposals",
    response_model=list[SupervisionProposal],
)
def read_contract_proposals(contract_id: str) -> list[SupervisionProposal]:
    if store.get_contract(contract_id) is None:
        raise HTTPException(status_code=404, detail="Supervision contract not found")
    return store.list_proposals(contract_id)


@app.post("/agent/events", response_model=CancellationOutcome)
def receive_agent_event(event: CaregiverCancellationEvent) -> CancellationOutcome:
    contract = store.get_contract(event.contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail="Supervision contract not found")
    caregiver = store.get_member(event.caregiver_id)
    if (
        caregiver is None
        or caregiver.family_id != contract.family_id
        or caregiver.role != MemberRole.CAREGIVER
    ):
        raise HTTPException(
            status_code=422,
            detail="caregiver_id is not a caregiver in the contract family",
        )
    try:
        return store.process_caregiver_cancellation(event)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except CancellationConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/agent/chat", response_model=AgentChatResponse)
def chat_with_agent(payload: AgentChatRequest) -> AgentChatResponse:
    require_local_workflow_mode()
    try:
        return agent.respond(payload.message)
    except AgentUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except AgentOutputError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


def require_local_workflow_mode() -> None:
    if store.environment not in {"development", "dev", "test", "testing"}:
        raise HTTPException(
            status_code=501,
            detail="This workflow requires authenticated identity, which is not configured.",
        )


def workflow_result(result: ProposalDecisionResult) -> ProposalDecisionResult:
    if result.invalidated_reason is not None:
        raise HTTPException(status_code=409, detail=result.invalidated_reason)
    return result


@app.get("/approvals/pending", response_model=list[Approval])
def read_pending_approvals() -> list[Approval]:
    require_local_workflow_mode()
    return store.list_pending_approvals()


@app.post("/approvals/{approval_id}/approve", response_model=ProposalDecisionResult)
def approve_proposal(
    approval_id: str,
    payload: ApprovalDecisionRequest,
) -> ProposalDecisionResult:
    require_local_workflow_mode()
    try:
        result = store.decide_approval(approval_id, payload.actor_id, approve=True)
    except WorkflowNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except WorkflowConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return workflow_result(result)


@app.post("/approvals/{approval_id}/reject", response_model=ProposalDecisionResult)
def reject_proposal(
    approval_id: str,
    payload: ApprovalDecisionRequest,
) -> ProposalDecisionResult:
    require_local_workflow_mode()
    try:
        result = store.decide_approval(approval_id, payload.actor_id, approve=False)
    except WorkflowNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except WorkflowConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return workflow_result(result)


@app.post("/proposals/{proposal_id}/accept", response_model=ProposalDecisionResult)
def accept_proposal(
    proposal_id: str,
    payload: ApprovalDecisionRequest,
) -> ProposalDecisionResult:
    require_local_workflow_mode()
    try:
        result = store.decide_caregiver_acceptance(
            proposal_id, payload.actor_id, accept=True
        )
    except WorkflowNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except WorkflowConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return workflow_result(result)


@app.post("/proposals/{proposal_id}/decline", response_model=ProposalDecisionResult)
def decline_proposal(
    proposal_id: str,
    payload: ApprovalDecisionRequest,
) -> ProposalDecisionResult:
    require_local_workflow_mode()
    try:
        result = store.decide_caregiver_acceptance(
            proposal_id, payload.actor_id, accept=False
        )
    except WorkflowNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except WorkflowConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return workflow_result(result)


@app.post(
    "/supervision/contracts/{contract_id}/confirm-handover",
    response_model=SupervisionContract,
)
def confirm_contract_handover(
    contract_id: str,
    payload: ApprovalDecisionRequest,
) -> SupervisionContract:
    require_local_workflow_mode()
    try:
        return store.confirm_handover(
            contract_id,
            payload.actor_id,
            correlation_id=f"handover-{uuid4().hex}",
        )
    except WorkflowNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except WorkflowConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post(
    "/supervision/contracts/{contract_id}/check-handover-timeout",
    response_model=SupervisionContract,
)
def check_contract_handover_timeout(contract_id: str) -> SupervisionContract:
    require_local_workflow_mode()
    try:
        return store.check_handover_timeout(
            contract_id,
            correlation_id=f"handover-timeout-{uuid4().hex}",
        )
    except WorkflowNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except WorkflowConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
