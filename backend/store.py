"""SQLite-backed repository for family supervision coordination state."""

from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
from typing import Iterator, Optional
from uuid import uuid4

from backend.models.supervision import (
    ActorType,
    AvailabilityStatus,
    Approval,
    ApprovalType,
    AuditEvent,
    CancellationOutcome,
    CaregiverAvailability,
    CaregiverCancellationEvent,
    ContractStatus,
    DecisionStatus,
    MemberRole,
    MemberStatus,
    Family,
    FamilyMember,
    SupervisionContract,
    SupervisionProposal,
    ProposalDecisionResult,
    ProposalStatus,
)


DEFAULT_DATABASE_PATH = Path(__file__).parent / "data" / "caresync.db"


class CancellationConflict(ValueError):
    """Raised when a cancellation event conflicts with current contract state."""


class WorkflowConflict(ValueError):
    """Raised when an approval or acceptance is invalid for the current workflow."""


class WorkflowNotFound(LookupError):
    """Raised when an approval or proposal does not exist."""


class SQLiteStore:
    def __init__(
        self,
        database_path: Optional[str] = None,
        environment: Optional[str] = None,
    ) -> None:
        configured_path = database_path or os.getenv(
            "CARESYNC_DATABASE_PATH", str(DEFAULT_DATABASE_PATH)
        )
        self.database_path = str(Path(configured_path).expanduser())
        self.environment = (environment or os.getenv("CARESYNC_ENV", "development")).lower()

        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)

        self._initialize_schema()
        if self.environment in {"development", "dev", "test", "testing"}:
            self._seed_demo_family()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize_schema(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS families (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS family_members (
                    id TEXT PRIMARY KEY,
                    family_id TEXT NOT NULL REFERENCES families(id),
                    role TEXT NOT NULL,
                    payload TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_family_members_family_id
                    ON family_members(family_id);

                CREATE TABLE IF NOT EXISTS supervision_contracts (
                    id TEXT PRIMARY KEY,
                    family_id TEXT NOT NULL REFERENCES families(id),
                    child_id TEXT NOT NULL REFERENCES family_members(id),
                    caregiver_id TEXT NOT NULL REFERENCES family_members(id),
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    status TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK(version >= 1),
                    payload TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_contracts_family_id
                    ON supervision_contracts(family_id);

                CREATE TABLE IF NOT EXISTS supervision_contract_versions (
                    contract_id TEXT NOT NULL REFERENCES supervision_contracts(id),
                    version INTEGER NOT NULL CHECK(version >= 1),
                    payload TEXT NOT NULL,
                    PRIMARY KEY (contract_id, version)
                );

                INSERT OR IGNORE INTO supervision_contract_versions
                    (contract_id, version, payload)
                SELECT id, version, payload FROM supervision_contracts;

                CREATE TABLE IF NOT EXISTS supervision_proposals (
                    id TEXT PRIMARY KEY,
                    original_contract_id TEXT NOT NULL
                        REFERENCES supervision_contracts(id),
                    proposed_caregiver_id TEXT NOT NULL REFERENCES family_members(id),
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_proposals_contract_id
                    ON supervision_proposals(original_contract_id);

                CREATE TABLE IF NOT EXISTS approvals (
                    id TEXT PRIMARY KEY,
                    proposal_id TEXT NOT NULL REFERENCES supervision_proposals(id),
                    approver_id TEXT NOT NULL REFERENCES family_members(id),
                    approval_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    UNIQUE (proposal_id, approval_type)
                );

                CREATE INDEX IF NOT EXISTS idx_approvals_status
                    ON approvals(status);

                CREATE TABLE IF NOT EXISTS caregiver_availability (
                    id TEXT PRIMARY KEY,
                    caregiver_id TEXT NOT NULL REFERENCES family_members(id),
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    availability_status TEXT NOT NULL,
                    payload TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_availability_caregiver_id
                    ON caregiver_availability(caregiver_id);

                CREATE TABLE IF NOT EXISTS caregiver_cancellation_events (
                    id TEXT PRIMARY KEY,
                    contract_id TEXT NOT NULL REFERENCES supervision_contracts(id),
                    caregiver_id TEXT NOT NULL REFERENCES family_members(id),
                    payload TEXT NOT NULL,
                    result_payload TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_events (
                    id TEXT PRIMARY KEY,
                    family_id TEXT NOT NULL REFERENCES families(id),
                    actor_id TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    payload TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_audit_events_resource_id
                    ON audit_events(resource_id, timestamp);
                """
            )
            self._migrate_audit_actor_ids(connection)

    @staticmethod
    def _migrate_audit_actor_ids(connection: sqlite3.Connection) -> None:
        foreign_keys = connection.execute("PRAGMA foreign_key_list(audit_events)").fetchall()
        if not any(row["from"] == "actor_id" for row in foreign_keys):
            return

        connection.execute("ALTER TABLE audit_events RENAME TO audit_events_legacy")
        connection.execute(
            """CREATE TABLE audit_events (
                id TEXT PRIMARY KEY,
                family_id TEXT NOT NULL REFERENCES families(id),
                actor_id TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                correlation_id TEXT NOT NULL,
                payload TEXT NOT NULL
            )"""
        )
        connection.execute(
            """INSERT INTO audit_events
               (id, family_id, actor_id, resource_id, timestamp, correlation_id, payload)
               SELECT id, family_id, actor_id, resource_id, timestamp, correlation_id, payload
               FROM audit_events_legacy"""
        )
        connection.execute("DROP TABLE audit_events_legacy")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_events_resource_id "
            "ON audit_events(resource_id, timestamp)"
        )

    def _seed_demo_family(self) -> None:
        family = Family(id="family-demo", name="Demo Family")
        members = (
            FamilyMember(
                id="guardian-demo",
                family_id=family.id,
                name="Demo Guardian",
                role="GUARDIAN",
            ),
            FamilyMember(
                id="caregiver-demo",
                family_id=family.id,
                name="Demo Caregiver",
                role="CAREGIVER",
            ),
            FamilyMember(
                id="caregiver-alt-demo",
                family_id=family.id,
                name="Demo Backup Caregiver",
                role="CAREGIVER",
            ),
            FamilyMember(
                id="child-demo",
                family_id=family.id,
                name="Demo Child",
                role="CHILD",
            ),
        )
        with self._connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO families (id, name, created_at, payload) VALUES (?, ?, ?, ?)",
                (family.id, family.name, family.created_at.isoformat(), family.model_dump_json()),
            )
            connection.executemany(
                "INSERT OR IGNORE INTO family_members (id, family_id, role, payload) "
                "VALUES (?, ?, ?, ?)",
                [
                    (member.id, member.family_id, member.role, member.model_dump_json())
                    for member in members
                ],
            )

    def get_family(self, family_id: str) -> Optional[Family]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload FROM families WHERE id = ?", (family_id,)
            ).fetchone()
        return Family.model_validate_json(row["payload"]) if row else None

    def list_family_members(self, family_id: str) -> list[FamilyMember]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT payload FROM family_members WHERE family_id = ? ORDER BY id",
                (family_id,),
            ).fetchall()
        return [FamilyMember.model_validate_json(row["payload"]) for row in rows]

    def get_member(self, member_id: str) -> Optional[FamilyMember]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload FROM family_members WHERE id = ?", (member_id,)
            ).fetchone()
        return FamilyMember.model_validate_json(row["payload"]) if row else None

    def get_agent_contract_context(self, contract_id: str) -> dict:
        contract = self.get_contract(contract_id)
        if contract is None:
            raise LookupError("Supervision contract not found")
        return {
            "id": contract.id,
            "family_id": contract.family_id,
            "child_id": contract.child_id,
            "caregiver_id": contract.caregiver_id,
            "activity": contract.activity,
            "expected_location": contract.expected_location,
            "start_time": contract.start_time.isoformat(),
            "end_time": contract.end_time.isoformat(),
            "status": contract.status,
            "version": contract.version,
        }

    def list_available_caregivers_for_contract(self, contract_id: str) -> list[dict]:
        with self._connection() as connection:
            contract_row = connection.execute(
                "SELECT payload FROM supervision_contracts WHERE id = ?",
                (contract_id,),
            ).fetchone()
            if contract_row is None:
                raise LookupError("Supervision contract not found")
            contract = SupervisionContract.model_validate_json(contract_row["payload"])
            return [
                {"caregiver_id": member.id, "name": member.name}
                for member in self._available_caregiver_members(connection, contract)
            ]

    def validate_replacement_candidate(
        self,
        contract_id: str,
        proposed_caregiver_id: str,
        original_contract_version: int,
    ) -> dict:
        with self._connection() as connection:
            contract_row = connection.execute(
                "SELECT payload FROM supervision_contracts WHERE id = ?",
                (contract_id,),
            ).fetchone()
            if contract_row is None:
                return {"valid": False, "reason": "Supervision contract was not found."}
            contract = SupervisionContract.model_validate_json(contract_row["payload"])
            if (
                contract.status != ContractStatus.CONFLICTED
                or contract.version != original_contract_version
            ):
                return {
                    "valid": False,
                    "reason": "The contract is no longer conflicted at the proposed version.",
                }
            caregiver_row = connection.execute(
                "SELECT payload FROM family_members WHERE id = ?",
                (proposed_caregiver_id,),
            ).fetchone()
            if caregiver_row is None:
                return {"valid": False, "reason": "Proposed caregiver was not found."}
            caregiver = FamilyMember.model_validate_json(caregiver_row["payload"])
            if (
                caregiver.family_id != contract.family_id
                or caregiver.role != MemberRole.CAREGIVER
                or caregiver.status != MemberStatus.ACTIVE
                or caregiver.id == contract.caregiver_id
            ):
                return {
                    "valid": False,
                    "reason": "Proposed member is not an active alternative caregiver in this family.",
                }
            if not self._caregiver_available_for(
                connection,
                caregiver.id,
                contract.start_time,
                contract.end_time,
            ):
                return {
                    "valid": False,
                    "reason": "Proposed caregiver is not available for the full contract interval.",
                }
            return {"valid": True, "reason": "Proposal is valid for the current contract version."}

    def create_contract(self, contract: SupervisionContract) -> None:
        with self._connection() as connection:
            self._insert_contract(connection, contract)

    def _insert_contract(
        self,
        connection: sqlite3.Connection,
        contract: SupervisionContract,
    ) -> None:
        connection.execute(
            """INSERT INTO supervision_contracts
               (id, family_id, child_id, caregiver_id, start_time, end_time,
                status, version, payload)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                contract.id,
                contract.family_id,
                contract.child_id,
                contract.caregiver_id,
                contract.start_time.isoformat(),
                contract.end_time.isoformat(),
                contract.status,
                contract.version,
                contract.model_dump_json(),
            ),
        )
        connection.execute(
            """INSERT INTO supervision_contract_versions
               (contract_id, version, payload) VALUES (?, ?, ?)""",
            (contract.id, contract.version, contract.model_dump_json()),
        )

    def get_contract(self, contract_id: str) -> Optional[SupervisionContract]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload FROM supervision_contracts WHERE id = ?", (contract_id,)
            ).fetchone()
        return SupervisionContract.model_validate_json(row["payload"]) if row else None

    def save_contract(self, contract: SupervisionContract) -> bool:
        with self._connection() as connection:
            return self._save_contract(connection, contract)

    def _save_contract(
        self,
        connection: sqlite3.Connection,
        contract: SupervisionContract,
    ) -> bool:
        current_row = connection.execute(
            "SELECT version, payload FROM supervision_contracts WHERE id = ?",
            (contract.id,),
        ).fetchone()
        if current_row is None:
            return False
        connection.execute(
            """INSERT OR IGNORE INTO supervision_contract_versions
               (contract_id, version, payload) VALUES (?, ?, ?)""",
            (contract.id, current_row["version"], current_row["payload"]),
        )
        cursor = connection.execute(
            """UPDATE supervision_contracts
               SET family_id = ?, child_id = ?, caregiver_id = ?, start_time = ?,
                   end_time = ?, status = ?, version = ?, payload = ?
               WHERE id = ?""",
            (
                contract.family_id,
                contract.child_id,
                contract.caregiver_id,
                contract.start_time.isoformat(),
                contract.end_time.isoformat(),
                contract.status,
                contract.version,
                contract.model_dump_json(),
                contract.id,
            ),
        )
        return cursor.rowcount > 0

    def get_contract_version(
        self, contract_id: str, version: int
    ) -> Optional[SupervisionContract]:
        with self._connection() as connection:
            row = connection.execute(
                """SELECT payload FROM supervision_contract_versions
                   WHERE contract_id = ? AND version = ?""",
                (contract_id, version),
            ).fetchone()
        return SupervisionContract.model_validate_json(row["payload"]) if row else None

    def list_proposals(self, contract_id: str) -> list[SupervisionProposal]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT payload FROM supervision_proposals
                   WHERE original_contract_id = ? ORDER BY id""",
                (contract_id,),
            ).fetchall()
        return [SupervisionProposal.model_validate_json(row["payload"]) for row in rows]

    def list_pending_approvals(self) -> list[Approval]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT payload FROM approvals WHERE status = ? ORDER BY id",
                (DecisionStatus.PENDING,),
            ).fetchall()
        return [Approval.model_validate_json(row["payload"]) for row in rows]

    def decide_approval(
        self,
        approval_id: str,
        actor_id: str,
        approve: bool,
    ) -> ProposalDecisionResult:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM approvals WHERE id = ?", (approval_id,)
            ).fetchone()
            if row is None:
                raise WorkflowNotFound("Approval not found")
            approval = Approval.model_validate_json(row["payload"])
            if approval.approval_type != ApprovalType.GUARDIAN:
                raise WorkflowConflict("This approval must be answered by the proposed caregiver")
            return self._record_workflow_decision(
                connection,
                approval,
                actor_id,
                DecisionStatus.APPROVED if approve else DecisionStatus.REJECTED,
            )

    def decide_caregiver_acceptance(
        self,
        proposal_id: str,
        actor_id: str,
        accept: bool,
    ) -> ProposalDecisionResult:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT payload FROM approvals
                   WHERE proposal_id = ? AND approval_type = ?""",
                (proposal_id, ApprovalType.CAREGIVER),
            ).fetchone()
            if row is None:
                raise WorkflowNotFound("Caregiver acceptance request not found")
            approval = Approval.model_validate_json(row["payload"])
            return self._record_workflow_decision(
                connection,
                approval,
                actor_id,
                DecisionStatus.ACCEPTED if accept else DecisionStatus.DECLINED,
            )

    def _record_workflow_decision(
        self,
        connection: sqlite3.Connection,
        approval: Approval,
        actor_id: str,
        decision: DecisionStatus,
    ) -> ProposalDecisionResult:
        if actor_id != approval.approver_id:
            raise WorkflowConflict("Actor does not match the assigned responder")
        if approval.status != DecisionStatus.PENDING:
            raise WorkflowConflict("This response has already been recorded")

        proposal_row = connection.execute(
            "SELECT payload FROM supervision_proposals WHERE id = ?",
            (approval.proposal_id,),
        ).fetchone()
        if proposal_row is None:
            raise WorkflowNotFound("Proposal not found")
        proposal = SupervisionProposal.model_validate_json(proposal_row["payload"])
        if proposal.status not in {
            ProposalStatus.PENDING,
            ProposalStatus.APPROVED,
            ProposalStatus.ACCEPTED,
        }:
            raise WorkflowConflict("Proposal is no longer awaiting responses")

        contract_row = connection.execute(
            "SELECT payload FROM supervision_contracts WHERE id = ?",
            (proposal.original_contract_id,),
        ).fetchone()
        if contract_row is None:
            raise WorkflowNotFound("Original supervision contract not found")
        contract = SupervisionContract.model_validate_json(contract_row["payload"])

        approval.response_timestamp = datetime.now(timezone.utc)
        approval.status = decision
        self._save_approval(connection, approval)

        if (
            contract.version != proposal.original_contract_version
            or contract.status != ContractStatus.CONFLICTED
        ):
            return self._invalidate_proposal(
                connection,
                approval,
                proposal,
                contract,
                "The original contract changed after this proposal was created.",
            )

        if decision in {DecisionStatus.REJECTED, DecisionStatus.DECLINED}:
            proposal.status = (
                ProposalStatus.REJECTED
                if decision == DecisionStatus.REJECTED
                else ProposalStatus.DECLINED
            )
            contract.status = ContractStatus.ESCALATED
            contract.version += 1
            contract.updated_at = datetime.now(timezone.utc)
            self._save_contract(connection, contract)
            self._save_proposal(connection, proposal)
            return ProposalDecisionResult(approval=approval, proposal=proposal)

        approval_rows = connection.execute(
            "SELECT payload FROM approvals WHERE proposal_id = ?",
            (proposal.id,),
        ).fetchall()
        approvals = [Approval.model_validate_json(row["payload"]) for row in approval_rows]
        guardian_approved = any(
            item.approval_type == ApprovalType.GUARDIAN
            and item.status == DecisionStatus.APPROVED
            for item in approvals
        )
        caregiver_accepted = any(
            item.approval_type == ApprovalType.CAREGIVER
            and item.status == DecisionStatus.ACCEPTED
            for item in approvals
        )

        if guardian_approved and caregiver_accepted:
            caregiver_row = connection.execute(
                "SELECT payload FROM family_members WHERE id = ?",
                (proposal.proposed_caregiver_id,),
            ).fetchone()
            if caregiver_row is None:
                return self._invalidate_proposal(
                    connection,
                    approval,
                    proposal,
                    contract,
                    "The proposed caregiver is no longer a family member.",
                )
            caregiver = FamilyMember.model_validate_json(caregiver_row["payload"])
            if (
                caregiver.family_id != contract.family_id
                or caregiver.role != MemberRole.CAREGIVER
                or caregiver.status != MemberStatus.ACTIVE
                or not self._caregiver_available_for(
                    connection,
                    caregiver.id,
                    contract.start_time,
                    contract.end_time,
                )
            ):
                return self._invalidate_proposal(
                    connection,
                    approval,
                    proposal,
                    contract,
                    "The proposed caregiver is no longer available for the full interval.",
                )

            replacement = SupervisionContract(
                id=f"contract-{uuid4().hex}",
                family_id=contract.family_id,
                child_id=contract.child_id,
                caregiver_id=caregiver.id,
                activity=contract.activity,
                expected_location=contract.expected_location,
                start_time=contract.start_time,
                end_time=contract.end_time,
                status=ContractStatus.CONFIRMED,
                approval_status=DecisionStatus.APPROVED,
                acceptance_status=DecisionStatus.ACCEPTED,
                version=1,
            )
            self._insert_contract(connection, replacement)
            proposal.status = ProposalStatus.CONFIRMED
            self._save_proposal(connection, proposal)
            return ProposalDecisionResult(
                approval=approval,
                proposal=proposal,
                replacement_contract=replacement,
            )

        proposal.status = (
            ProposalStatus.APPROVED if guardian_approved else ProposalStatus.ACCEPTED
        )
        self._save_proposal(connection, proposal)
        return ProposalDecisionResult(approval=approval, proposal=proposal)

    def _invalidate_proposal(
        self,
        connection: sqlite3.Connection,
        current_approval: Approval,
        proposal: SupervisionProposal,
        contract: SupervisionContract,
        reason: str,
    ) -> ProposalDecisionResult:
        proposal.status = ProposalStatus.INVALIDATED
        pending_rows = connection.execute(
            "SELECT payload FROM approvals WHERE proposal_id = ? AND status = ?",
            (proposal.id, DecisionStatus.PENDING),
        ).fetchall()
        for row in pending_rows:
            pending = Approval.model_validate_json(row["payload"])
            pending.status = DecisionStatus.INVALIDATED
            self._save_approval(connection, pending)
        if contract.status not in {
            ContractStatus.ESCALATED,
            ContractStatus.CANCELLED,
            ContractStatus.COMPLETED,
        }:
            contract.status = ContractStatus.ESCALATED
            contract.version += 1
            contract.updated_at = datetime.now(timezone.utc)
            self._save_contract(connection, contract)
        self._save_proposal(connection, proposal)
        return ProposalDecisionResult(
            approval=current_approval,
            proposal=proposal,
            invalidated_reason=reason,
        )

    def _save_approval(self, connection: sqlite3.Connection, approval: Approval) -> None:
        connection.execute(
            "UPDATE approvals SET status = ?, payload = ? WHERE id = ?",
            (approval.status, approval.model_dump_json(), approval.id),
        )

    def _save_proposal(
        self,
        connection: sqlite3.Connection,
        proposal: SupervisionProposal,
    ) -> None:
        connection.execute(
            "UPDATE supervision_proposals SET status = ?, payload = ? WHERE id = ?",
            (proposal.status, proposal.model_dump_json(), proposal.id),
        )

    def _caregiver_available_for(
        self,
        connection: sqlite3.Connection,
        caregiver_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> bool:
        rows = connection.execute(
            "SELECT payload FROM caregiver_availability WHERE caregiver_id = ?",
            (caregiver_id,),
        ).fetchall()
        windows = [
            CaregiverAvailability.model_validate_json(row["payload"]) for row in rows
        ]
        return any(
            window.availability_status == AvailabilityStatus.AVAILABLE
            and window.start_time <= start_time
            and window.end_time >= end_time
            for window in windows
        ) and not any(
            window.availability_status == AvailabilityStatus.UNAVAILABLE
            and window.start_time < end_time
            and window.end_time > start_time
            for window in windows
        )

    def _available_caregiver_members(
        self,
        connection: sqlite3.Connection,
        contract: SupervisionContract,
    ) -> list[FamilyMember]:
        rows = connection.execute(
            """SELECT payload FROM family_members
               WHERE family_id = ? ORDER BY id""",
            (contract.family_id,),
        ).fetchall()
        available = []
        for row in rows:
            member = FamilyMember.model_validate_json(row["payload"])
            if (
                member.role == MemberRole.CAREGIVER
                and member.status == MemberStatus.ACTIVE
                and member.id != contract.caregiver_id
                and self._caregiver_available_for(
                    connection,
                    member.id,
                    contract.start_time,
                    contract.end_time,
                )
            ):
                available.append(member)
        return available

    def confirm_handover(
        self,
        contract_id: str,
        actor_id: str,
        correlation_id: str,
    ) -> SupervisionContract:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM supervision_contracts WHERE id = ?",
                (contract_id,),
            ).fetchone()
            if row is None:
                raise WorkflowNotFound("Supervision contract not found")
            contract = SupervisionContract.model_validate_json(row["payload"])

            if actor_id != contract.caregiver_id:
                raise WorkflowConflict("Only the assigned caregiver can confirm this handover")
            caregiver_row = connection.execute(
                "SELECT payload FROM family_members WHERE id = ?",
                (actor_id,),
            ).fetchone()
            if caregiver_row is None:
                raise WorkflowConflict("Assigned caregiver is not a family member")
            caregiver = FamilyMember.model_validate_json(caregiver_row["payload"])
            if (
                caregiver.family_id != contract.family_id
                or caregiver.role != MemberRole.CAREGIVER
                or caregiver.status != MemberStatus.ACTIVE
            ):
                raise WorkflowConflict("Assigned caregiver is not active in this family")

            if (
                contract.status == ContractStatus.ACTIVE
                and contract.handover_status == "CONFIRMED"
            ):
                return contract
            if contract.status != ContractStatus.CONFIRMED:
                raise WorkflowConflict("Contract is not confirmed and ready for handover")

            confirmed_at = datetime.now(timezone.utc)
            if confirmed_at < contract.start_time:
                raise WorkflowConflict("Contract has not started yet")
            if confirmed_at >= contract.end_time:
                raise WorkflowConflict("Contract supervision window has ended")

            contract.handover_status = "CONFIRMED"
            contract.status = ContractStatus.ACTIVE
            contract.version += 1
            contract.updated_at = confirmed_at
            if not self._save_contract(connection, contract):
                raise WorkflowNotFound("Supervision contract not found")

            audit_event = AuditEvent(
                id=f"audit-{uuid4().hex}",
                family_id=contract.family_id,
                actor_id=actor_id,
                actor_type=ActorType.USER,
                action="HANDOVER_CONFIRMED",
                resource_id=contract.id,
                timestamp=confirmed_at,
                correlation_id=correlation_id,
            )
            connection.execute(
                """INSERT INTO audit_events
                   (id, family_id, actor_id, resource_id, timestamp, correlation_id, payload)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    audit_event.id,
                    audit_event.family_id,
                    audit_event.actor_id,
                    audit_event.resource_id,
                    audit_event.timestamp.isoformat(),
                    audit_event.correlation_id,
                    audit_event.model_dump_json(),
                ),
            )
        return contract

    def check_handover_timeout(
        self,
        contract_id: str,
        correlation_id: str,
    ) -> SupervisionContract:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM supervision_contracts WHERE id = ?",
                (contract_id,),
            ).fetchone()
            if row is None:
                raise WorkflowNotFound("Supervision contract not found")
            contract = SupervisionContract.model_validate_json(row["payload"])

            # A timeout check can be safely retried after a terminal outcome.
            if contract.status in {
                ContractStatus.ACTIVE,
                ContractStatus.ESCALATED,
                ContractStatus.CANCELLED,
                ContractStatus.COMPLETED,
            }:
                return contract
            if (
                contract.status != ContractStatus.CONFIRMED
                or contract.handover_status != "PENDING"
            ):
                raise WorkflowConflict("Contract is not awaiting handover")

            checked_at = datetime.now(timezone.utc)
            if checked_at < contract.start_time:
                raise WorkflowConflict("Handover timeout is not due yet")

            contract.status = ContractStatus.ESCALATED
            contract.version += 1
            contract.updated_at = checked_at
            if not self._save_contract(connection, contract):
                raise WorkflowNotFound("Supervision contract not found")

            audit_event = AuditEvent(
                id=f"audit-{uuid4().hex}",
                family_id=contract.family_id,
                actor_id="system",
                actor_type=ActorType.SYSTEM,
                action="HANDOVER_TIMEOUT_ESCALATED",
                resource_id=contract.id,
                timestamp=checked_at,
                correlation_id=correlation_id,
            )
            connection.execute(
                """INSERT INTO audit_events
                   (id, family_id, actor_id, resource_id, timestamp, correlation_id, payload)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    audit_event.id,
                    audit_event.family_id,
                    audit_event.actor_id,
                    audit_event.resource_id,
                    audit_event.timestamp.isoformat(),
                    audit_event.correlation_id,
                    audit_event.model_dump_json(),
                ),
            )
        return contract

    def list_audit_events(self, resource_id: str) -> list[AuditEvent]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT payload FROM audit_events
                   WHERE resource_id = ? ORDER BY timestamp, id""",
                (resource_id,),
            ).fetchall()
        return [AuditEvent.model_validate_json(row["payload"]) for row in rows]

    def create_availability(self, availability: CaregiverAvailability) -> None:
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO caregiver_availability
                   (id, caregiver_id, start_time, end_time, availability_status, payload)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    availability.id,
                    availability.caregiver_id,
                    availability.start_time.isoformat(),
                    availability.end_time.isoformat(),
                    availability.availability_status,
                    availability.model_dump_json(),
                ),
            )

    def list_caregiver_availability(self, caregiver_id: str) -> list[CaregiverAvailability]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT payload FROM caregiver_availability
                   WHERE caregiver_id = ? ORDER BY start_time, id""",
                (caregiver_id,),
            ).fetchall()
        return [CaregiverAvailability.model_validate_json(row["payload"]) for row in rows]

    def process_caregiver_cancellation(
        self, event: CaregiverCancellationEvent
    ) -> CancellationOutcome:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")

            existing = connection.execute(
                """SELECT payload, result_payload FROM caregiver_cancellation_events
                   WHERE id = ?""",
                (event.id,),
            ).fetchone()
            if existing is not None:
                previous_event = CaregiverCancellationEvent.model_validate_json(
                    existing["payload"]
                )
                if previous_event != event:
                    raise CancellationConflict(
                        "event id was already used for a different cancellation"
                    )
                outcome = CancellationOutcome.model_validate_json(
                    existing["result_payload"]
                )
                return outcome.model_copy(update={"duplicate": True})

            contract_row = connection.execute(
                "SELECT payload FROM supervision_contracts WHERE id = ?",
                (event.contract_id,),
            ).fetchone()
            if contract_row is None:
                raise LookupError("Supervision contract not found")
            contract = SupervisionContract.model_validate_json(contract_row["payload"])
            if contract.caregiver_id != event.caregiver_id:
                raise CancellationConflict(
                    "cancellation caregiver does not match the assigned caregiver"
                )
            if contract.status in {
                ContractStatus.CONFLICTED,
                ContractStatus.ESCALATED,
                ContractStatus.CANCELLED,
                ContractStatus.COMPLETED,
            }:
                raise CancellationConflict(
                    "contract is already conflicted or no longer active"
                )

            candidate_rows = connection.execute(
                """SELECT payload FROM family_members
                   WHERE family_id = ? ORDER BY id""",
                (contract.family_id,),
            ).fetchall()
            candidates = []
            guardians = []
            for row in candidate_rows:
                member = FamilyMember.model_validate_json(row["payload"])
                if member.role == MemberRole.GUARDIAN and member.status == MemberStatus.ACTIVE:
                    guardians.append(member)
                if (
                    member.role != MemberRole.CAREGIVER
                    or member.status != MemberStatus.ACTIVE
                    or member.id == contract.caregiver_id
                ):
                    continue

                if self._caregiver_available_for(
                    connection,
                    member.id,
                    contract.start_time,
                    contract.end_time,
                ):
                    candidates.append(member)

            contract.version += 1
            contract.updated_at = datetime.now(timezone.utc)
            proposal = None
            if candidates and guardians:
                contract.status = ContractStatus.CONFLICTED
                proposal = SupervisionProposal(
                    id=f"proposal-{uuid4().hex}",
                    original_contract_id=contract.id,
                    original_contract_version=contract.version,
                    proposed_caregiver_id=candidates[0].id,
                    proposed_start_time=contract.start_time,
                    proposed_end_time=contract.end_time,
                    reason=event.reason,
                )
                connection.execute(
                    """INSERT INTO supervision_proposals
                       (id, original_contract_id, proposed_caregiver_id, status, payload)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        proposal.id,
                        proposal.original_contract_id,
                        proposal.proposed_caregiver_id,
                        proposal.status,
                        proposal.model_dump_json(),
                    ),
                )
                approval_records = (
                    Approval(
                        id=f"approval-{uuid4().hex}",
                        proposal_id=proposal.id,
                        approver_id=guardians[0].id,
                        approval_type=ApprovalType.GUARDIAN,
                    ),
                    Approval(
                        id=f"approval-{uuid4().hex}",
                        proposal_id=proposal.id,
                        approver_id=proposal.proposed_caregiver_id,
                        approval_type=ApprovalType.CAREGIVER,
                    ),
                )
                connection.executemany(
                    """INSERT INTO approvals
                       (id, proposal_id, approver_id, approval_type, status, payload)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    [
                        (
                            approval.id,
                            approval.proposal_id,
                            approval.approver_id,
                            approval.approval_type,
                            approval.status,
                            approval.model_dump_json(),
                        )
                        for approval in approval_records
                    ],
                )
                message = "Replacement caregiver proposal created; guardian approval is required."
            else:
                contract.status = ContractStatus.ESCALATED
                message = "No caregiver is available for the full interval; guardian action is required."

            if not self._save_contract(connection, contract):
                raise LookupError("Supervision contract not found")
            outcome = CancellationOutcome(
                event_id=event.id,
                contract_id=contract.id,
                contract_status=contract.status,
                contract_version=contract.version,
                proposal=proposal,
                requires_guardian_action=True,
                message=message,
            )
            connection.execute(
                """INSERT INTO caregiver_cancellation_events
                   (id, contract_id, caregiver_id, payload, result_payload)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    event.id,
                    event.contract_id,
                    event.caregiver_id,
                    event.model_dump_json(),
                    outcome.model_dump_json(),
                ),
            )
        return outcome


store = SQLiteStore()
