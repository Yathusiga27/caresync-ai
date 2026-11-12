from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI, HTTPException, status

from backend.models.supervision import (
    ContractStatus,
    DecisionStatus,
    FamilyMember,
    MemberRole,
    SupervisionProposal,
    SupervisionContract,
)
from backend.schemas import ContractCreate
from backend.store import store

app = FastAPI(
    title="CareSync AI",
    description="A family supervision coordination application.",
    version="0.1.0",
)


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
