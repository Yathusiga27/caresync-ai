"""Add optional fictional schedules to a local development database."""

from datetime import datetime, timedelta, timezone

from backend.models.supervision import ContractStatus, DecisionStatus, SupervisionContract
from backend.store import WorkflowConflict, store


def main() -> None:
    if store.environment not in {"development", "dev", "test", "testing"}:
        raise SystemExit("Sample schedules require a development or test database.")

    now = datetime.now(timezone.utc)
    samples = (
        ("sample-school-pickup", "School pickup", "School entrance", "caregiver-alt-demo",
         now + timedelta(days=1), False),
        ("sample-after-school-care", "After-school care", "Family home", "caregiver-demo",
         now + timedelta(hours=1), True),
    )
    for contract_id, activity, location, caregiver_id, start, confirm in samples:
        if store.get_contract(contract_id):
            print(f"Already present: {activity}")
            continue
        contract = SupervisionContract(
            id=contract_id,
            family_id="family-demo",
            child_id="child-demo",
            caregiver_id=caregiver_id,
            guardian_id="guardian-demo",
            activity=activity,
            expected_location=location,
            start_time=start,
            end_time=start + timedelta(hours=1),
            status=ContractStatus.PENDING_APPROVAL,
        )
        try:
            store.create_contract(contract)
        except WorkflowConflict as error:
            print(f"Skipped {activity}: {error}")
            continue
        if confirm:
            contract = store.record_schedule_decision(contract.id, caregiver_id, DecisionStatus.ACCEPTED)
        print(f"Added {activity}: {contract.status}")


if __name__ == "__main__":
    main()
