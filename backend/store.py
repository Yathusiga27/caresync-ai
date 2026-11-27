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
    GuardianNotification,
    GuardianNotificationKind,
    SupervisionContract,
    SupervisionProposal,
    ProposalDecisionResult,
    ProposalStatus,
    ScheduleRequest,
)


DEFAULT_DATABASE_PATH = Path(__file__).parent / "data" / "caresync.db"
AWAITING_RESPONSE_STATUSES = (
    ProposalStatus.PENDING,
    ProposalStatus.APPROVED,
    ProposalStatus.ACCEPTED,
)
PROPOSAL_EXPIRY_REASON = "The replacement proposal expired before all required responses arrived."


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

                CREATE TABLE IF NOT EXISTS guardian_notifications (
                    id TEXT PRIMARY KEY,
                    guardian_id TEXT NOT NULL REFERENCES family_members(id),
                    contract_id TEXT NOT NULL REFERENCES supervision_contracts(id),
                    kind TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (contract_id, kind)
                );

                CREATE INDEX IF NOT EXISTS idx_guardian_notifications_inbox
                    ON guardian_notifications(guardian_id, created_at DESC, id);

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
            connection.execute("BEGIN IMMEDIATE")
            self._migrate_audit_actor_ids(connection)
            self._migrate_missed_handover_notifications(connection)
            self._migrate_initial_schedules(connection)

    def _migrate_initial_schedules(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            """SELECT payload FROM supervision_contracts
               WHERE (status = ? AND json_extract(payload, '$.approval_status') = ?)
                  OR (status = ? AND json_extract(payload, '$.acceptance_status') = ?
                      AND json_type(payload, '$.decline_reason') IS NULL)""",
            (
                ContractStatus.PENDING_APPROVAL, DecisionStatus.PENDING,
                ContractStatus.CANCELLED, DecisionStatus.DECLINED,
            ),
        ).fetchall()
        now = datetime.now(timezone.utc)
        for row in rows:
            contract = SupervisionContract.model_validate_json(row["payload"])
            contract.guardian_id = contract.guardian_id or self._schedule_guardian_id(connection, contract)
            if contract.status == ContractStatus.CANCELLED:
                contract.status = ContractStatus.DECLINED
            else:
                contract.approval_status = DecisionStatus.APPROVED
                if contract.acceptance_status == DecisionStatus.ACCEPTED:
                    contract.status = (
                        ContractStatus.CONFIRMED if now < contract.end_time else ContractStatus.EXPIRED
                    )
            contract.version += 1
            contract.updated_at = now
            self._save_contract(connection, contract)

    @staticmethod
    def _migrate_missed_handover_notifications(connection: sqlite3.Connection) -> None:
        legacy_table = connection.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type = 'table' AND name = 'missed_handover_notifications'"""
        ).fetchone()
        if legacy_table is None:
            return
        connection.execute(
            """INSERT INTO guardian_notifications
               (id, guardian_id, contract_id, kind, message, created_at)
               SELECT id, guardian_id, contract_id, ?, message, created_at
               FROM missed_handover_notifications""",
            (GuardianNotificationKind.MISSED_HANDOVER,),
        )
        connection.execute("DROP TABLE missed_handover_notifications")

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

    def create_family_member(self, member: FamilyMember) -> None:
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO family_members (id, family_id, role, payload) VALUES (?, ?, ?, ?)",
                (member.id, member.family_id, member.role, member.model_dump_json()),
            )

    def set_caregiver_scheduling_availability(
        self, family_id: str, member_id: str, available: bool
    ) -> FamilyMember:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM family_members WHERE id = ? AND family_id = ?",
                (member_id, family_id),
            ).fetchone()
            if row is None:
                raise WorkflowNotFound("Family member not found")
            member = FamilyMember.model_validate_json(row["payload"])
            if member.role != MemberRole.CAREGIVER or member.status != MemberStatus.ACTIVE:
                raise WorkflowConflict("Only active caregivers can set scheduling availability")
            member.available_for_scheduling = available
            connection.execute(
                "UPDATE family_members SET payload = ? WHERE id = ? AND family_id = ?",
                (member.model_dump_json(), member_id, family_id),
            )
            return member

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
                caregiver,
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
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM family_members WHERE id = ?", (contract.caregiver_id,)
            ).fetchone()
            if row is not None:
                caregiver = FamilyMember.model_validate_json(row["payload"])
                if caregiver.status != MemberStatus.ACTIVE or not caregiver.available_for_scheduling:
                    raise WorkflowConflict("This caregiver is unavailable for new schedules")
            if contract.status == ContractStatus.PENDING_APPROVAL:
                guardian_id = self._schedule_guardian_id(connection, contract)
                if contract.guardian_id and guardian_id is None:
                    raise WorkflowConflict("The guardian must be an active member of this family")
                contract.guardian_id = guardian_id
                contract.approval_status = DecisionStatus.APPROVED
            self._insert_contract(connection, contract)

    def _schedule_guardian_id(
        self, connection: sqlite3.Connection, contract: SupervisionContract
    ) -> Optional[str]:
        row = connection.execute(
            """SELECT id FROM family_members
               WHERE family_id = ? AND role = ?
                 AND COALESCE(json_extract(payload, '$.status'), 'ACTIVE') = 'ACTIVE'
                 AND (? IS NULL OR id = ?)
               ORDER BY id LIMIT 1""",
            (contract.family_id, MemberRole.GUARDIAN, contract.guardian_id, contract.guardian_id),
        ).fetchone()
        return row["id"] if row else None

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

    def list_family_contracts(self, family_id: str) -> list[SupervisionContract]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT payload FROM supervision_contracts WHERE family_id = ?",
                (family_id,),
            ).fetchall()
        contracts = [SupervisionContract.model_validate_json(row["payload"]) for row in rows]
        return sorted(contracts, key=lambda contract: (contract.start_time, contract.id))

    def cancel_contract(self, contract_id: str) -> SupervisionContract:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM supervision_contracts WHERE id = ?", (contract_id,)
            ).fetchone()
            if row is None:
                raise WorkflowNotFound("Supervision contract not found")
            contract = SupervisionContract.model_validate_json(row["payload"])
            if contract.status in {ContractStatus.COMPLETED, ContractStatus.CANCELLED}:
                raise WorkflowConflict("Contract cannot be cancelled in its current state")
            contract.status = ContractStatus.CANCELLED
            contract.version += 1
            contract.updated_at = datetime.now(timezone.utc)
            self._save_contract(connection, contract)
            return contract

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

    @staticmethod
    def _can_respond_to_schedule(contract: SupervisionContract, actor: FamilyMember) -> bool:
        return (
            actor.status == MemberStatus.ACTIVE
            and actor.role == MemberRole.CAREGIVER
            and actor.id == contract.caregiver_id
            and contract.acceptance_status == DecisionStatus.PENDING
        )

    def list_family_schedule_requests(self, family_id: str, actor_id: str) -> list[ScheduleRequest]:
        now = datetime.now(timezone.utc)
        with self._connection() as connection:
            actor = self._family_actor(connection, family_id, actor_id)
            if actor.status != MemberStatus.ACTIVE:
                return []
            rows = connection.execute(
                "SELECT payload FROM supervision_contracts WHERE family_id = ? AND status = ?",
                (family_id, ContractStatus.PENDING_APPROVAL),
            ).fetchall()
            requests = []
            for row in rows:
                contract = SupervisionContract.model_validate_json(row["payload"])
                if now >= contract.end_time:
                    continue
                if self._can_respond_to_schedule(contract, actor):
                    requests.append(ScheduleRequest(contract=contract, approval_type=ApprovalType.CAREGIVER))

            rows = connection.execute(
                """SELECT a.payload AS approval, p.payload AS proposal, c.payload AS contract
                   FROM approvals AS a
                   JOIN supervision_proposals AS p ON p.id = a.proposal_id
                   JOIN supervision_contracts AS c ON c.id = p.original_contract_id
                   WHERE c.family_id = ? AND c.status = ? AND a.approver_id = ?
                     AND a.status = ? AND p.status IN (?, ?, ?)""",
                (family_id, ContractStatus.CONFLICTED, actor_id, DecisionStatus.PENDING,
                 *AWAITING_RESPONSE_STATUSES),
            ).fetchall()
            for row in rows:
                proposal = SupervisionProposal.model_validate_json(row["proposal"])
                if now >= proposal.response_deadline:
                    continue
                contract = SupervisionContract.model_validate_json(row["contract"])
                approval = Approval.model_validate_json(row["approval"])
                requests.append(ScheduleRequest(
                    contract=contract, approval_type=approval.approval_type,
                    replacement_approval=approval,
                    proposed_caregiver_id=proposal.proposed_caregiver_id,
                ))
        return sorted(requests, key=lambda request: (request.contract.start_time, request.contract.id))

    @staticmethod
    def _family_actor(
        connection: sqlite3.Connection, family_id: str, actor_id: str
    ) -> FamilyMember:
        row = connection.execute(
            "SELECT payload FROM family_members WHERE family_id = ? AND id = ?",
            (family_id, actor_id),
        ).fetchone()
        if row is None:
            raise WorkflowNotFound("Family member not found")
        return FamilyMember.model_validate_json(row["payload"])

    def record_schedule_decision(
        self, contract_id: str, actor_id: str, decision: DecisionStatus, *,
        reason: Optional[str] = None, expected_version: Optional[int] = None,
    ) -> SupervisionContract:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM supervision_contracts WHERE id = ?", (contract_id,)
            ).fetchone()
            if row is None:
                raise WorkflowNotFound("Supervision contract not found")
            contract = SupervisionContract.model_validate_json(row["payload"])
            if contract.status != ContractStatus.PENDING_APPROVAL:
                raise WorkflowConflict("This schedule is no longer awaiting a caregiver response")
            if expected_version is not None and contract.version != expected_version:
                raise WorkflowConflict("This request has changed. Review the current request and try again")
            actor = self._family_actor(connection, contract.family_id, actor_id)
            if not self._can_respond_to_schedule(contract, actor):
                raise WorkflowConflict("This member has no pending response for this schedule")

            now = datetime.now(timezone.utc)
            if now >= contract.end_time:
                raise WorkflowConflict("The supervision window has ended")
            if decision not in {DecisionStatus.ACCEPTED, DecisionStatus.DECLINED}:
                raise WorkflowConflict("The caregiver must accept or decline")
            if decision == DecisionStatus.ACCEPTED:
                if not actor.available_for_scheduling:
                    raise WorkflowConflict("This caregiver is unavailable for new schedules")
                contract.status = ContractStatus.CONFIRMED
            else:
                contract.status = ContractStatus.DECLINED
                contract.decline_reason = (reason or "").strip() or None

            contract.guardian_id = contract.guardian_id or self._schedule_guardian_id(connection, contract)
            contract.approval_status = DecisionStatus.APPROVED
            contract.acceptance_status = decision
            contract.version += 1
            contract.updated_at = now
            self._save_contract(connection, contract)
            self._insert_audit_event(
                connection, contract.family_id, actor.id, ActorType.USER,
                f"SCHEDULE_CAREGIVER_{decision.value}", contract.id, contract.id, now,
            )
            if contract.status == ContractStatus.CONFIRMED:
                self._insert_audit_event(
                    connection, contract.family_id, "system", ActorType.SYSTEM,
                    "SCHEDULE_CONFIRMED", contract.id, contract.id, now,
                )
            return contract

    def request_schedule_caregiver(
        self, contract_id: str, actor_id: str, caregiver_id: str, expected_version: int,
    ) -> SupervisionContract:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM supervision_contracts WHERE id = ?", (contract_id,)
            ).fetchone()
            if row is None:
                raise WorkflowNotFound("Supervision contract not found")
            contract = SupervisionContract.model_validate_json(row["payload"])
            if contract.version != expected_version or contract.status != ContractStatus.DECLINED:
                raise WorkflowConflict("Only the current declined request can be reassigned")
            if actor_id != self._schedule_guardian_id(connection, contract):
                raise WorkflowConflict("Only the guardian who created this schedule can reassign it")

            now = datetime.now(timezone.utc)
            if now >= contract.end_time:
                raise WorkflowConflict("The supervision window has ended")
            if caregiver_id == contract.caregiver_id:
                raise WorkflowConflict("Choose another caregiver for this request")
            caregiver = self._family_actor(connection, contract.family_id, caregiver_id)
            if (
                caregiver.role != MemberRole.CAREGIVER or caregiver.status != MemberStatus.ACTIVE
                or not caregiver.available_for_scheduling
            ):
                raise WorkflowConflict("This caregiver is unavailable for new schedules")

            contract.guardian_id = actor_id
            contract.caregiver_id = caregiver_id
            contract.status = ContractStatus.PENDING_APPROVAL
            contract.approval_status = DecisionStatus.APPROVED
            contract.acceptance_status = DecisionStatus.PENDING
            contract.decline_reason = None
            contract.version += 1
            contract.updated_at = now
            self._save_contract(connection, contract)
            self._insert_audit_event(
                connection, contract.family_id, actor_id, ActorType.USER,
                "SCHEDULE_CAREGIVER_REQUESTED", contract.id, contract.id, now,
            )
            return contract

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
        if approval.status == DecisionStatus.EXPIRED:
            raise WorkflowConflict(PROPOSAL_EXPIRY_REASON)
        if approval.status != DecisionStatus.PENDING:
            raise WorkflowConflict("This response has already been recorded")

        proposal_row = connection.execute(
            "SELECT payload FROM supervision_proposals WHERE id = ?",
            (approval.proposal_id,),
        ).fetchone()
        if proposal_row is None:
            raise WorkflowNotFound("Proposal not found")
        proposal = SupervisionProposal.model_validate_json(proposal_row["payload"])
        if proposal.status not in AWAITING_RESPONSE_STATUSES:
            raise WorkflowConflict("Proposal is no longer awaiting responses")

        contract_row = connection.execute(
            "SELECT payload FROM supervision_contracts WHERE id = ?",
            (proposal.original_contract_id,),
        ).fetchone()
        if contract_row is None:
            raise WorkflowNotFound("Original supervision contract not found")
        contract = SupervisionContract.model_validate_json(contract_row["payload"])

        responded_at = datetime.now(timezone.utc)
        if responded_at >= proposal.response_deadline:
            self._expire_proposal(connection, proposal, contract, responded_at)
            approval.status = DecisionStatus.EXPIRED
            return ProposalDecisionResult(approval=approval, proposal=proposal)

        approval.response_timestamp = responded_at
        approval.status = decision
        self._save_approval(connection, approval)
        approval_type = ApprovalType(approval.approval_type)
        self._insert_audit_event(
            connection, contract.family_id, actor_id, ActorType.USER,
            f"{approval_type.value}_{decision.value}",
            proposal.id, proposal.id, responded_at,
        )

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
            if decision == DecisionStatus.REJECTED:
                proposal.status = ProposalStatus.REJECTED
                reason = "The guardian rejected the replacement proposal."
            else:
                proposal.status = ProposalStatus.DECLINED
                reason = "The proposed caregiver declined the replacement proposal."
            self._save_proposal(connection, proposal)
            self._escalate_failed_proposal(connection, contract, proposal, reason)
            return ProposalDecisionResult(approval=approval, proposal=proposal)

        approval_rows = connection.execute(
            "SELECT payload FROM approvals WHERE proposal_id = ?",
            (proposal.id,),
        ).fetchall()
        approvals = [Approval.model_validate_json(row["payload"]) for row in approval_rows]
        guardian_approval = next((
            item for item in approvals
            if item.approval_type == ApprovalType.GUARDIAN and item.status == DecisionStatus.APPROVED
        ), None)
        caregiver_accepted = any(
            item.approval_type == ApprovalType.CAREGIVER
            and item.status == DecisionStatus.ACCEPTED
            for item in approvals
        )

        if guardian_approval and caregiver_accepted:
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
                    caregiver,
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
                guardian_id=guardian_approval.approver_id,
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
            self._insert_audit_event(
                connection, contract.family_id, "system", ActorType.SYSTEM,
                "REPLACEMENT_CONFIRMED", replacement.id, proposal.id, replacement.created_at,
            )
            return ProposalDecisionResult(
                approval=approval,
                proposal=proposal,
                replacement_contract=replacement,
            )

        proposal.status = (
            ProposalStatus.APPROVED if guardian_approval else ProposalStatus.ACCEPTED
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
        self._close_pending_approvals(connection, proposal.id, DecisionStatus.INVALIDATED)
        if contract.status not in {
            ContractStatus.ESCALATED,
            ContractStatus.CANCELLED,
            ContractStatus.COMPLETED,
        }:
            self._escalate_failed_proposal(connection, contract, proposal, reason)
        self._save_proposal(connection, proposal)
        self._insert_audit_event(
            connection, contract.family_id, "system", ActorType.SYSTEM,
            "PROPOSAL_INVALIDATED", proposal.id, proposal.id, datetime.now(timezone.utc),
        )
        return ProposalDecisionResult(
            approval=current_approval,
            proposal=proposal,
            invalidated_reason=reason,
        )

    def _escalate_failed_proposal(
        self,
        connection: sqlite3.Connection,
        contract: SupervisionContract,
        proposal: SupervisionProposal,
        reason: str,
    ) -> None:
        contract.status = ContractStatus.ESCALATED
        contract.version += 1
        contract.updated_at = datetime.now(timezone.utc)
        self._save_contract(connection, contract)
        self._insert_audit_event(
            connection, contract.family_id, "system", ActorType.SYSTEM,
            "SUPERVISION_ESCALATED", contract.id, proposal.id, contract.updated_at,
        )
        self._insert_guardian_notification(
            connection,
            contract,
            GuardianNotificationKind.REPLACEMENT_PROPOSAL_FAILED,
            f"{reason} Please arrange supervision for the scheduled window.",
            proposal_id=proposal.id,
        )

    def _save_approval(self, connection: sqlite3.Connection, approval: Approval) -> None:
        connection.execute(
            "UPDATE approvals SET status = ?, payload = ? WHERE id = ?",
            (approval.status, approval.model_dump_json(), approval.id),
        )

    def _close_pending_approvals(
        self, connection: sqlite3.Connection, proposal_id: str, status: DecisionStatus
    ) -> None:
        rows = connection.execute(
            "SELECT payload FROM approvals WHERE proposal_id = ? AND status = ?",
            (proposal_id, DecisionStatus.PENDING),
        ).fetchall()
        for row in rows:
            approval = Approval.model_validate_json(row["payload"])
            approval.status = status
            self._save_approval(connection, approval)

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
        caregiver: FamilyMember,
        start_time: datetime,
        end_time: datetime,
    ) -> bool:
        if not caregiver.available_for_scheduling:
            return False
        rows = connection.execute(
            "SELECT payload FROM caregiver_availability WHERE caregiver_id = ?",
            (caregiver.id,),
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
                    member,
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

            self._insert_audit_event(
                connection, contract.family_id, actor_id, ActorType.USER,
                "HANDOVER_CONFIRMED", contract.id, correlation_id, confirmed_at,
            )
        return contract

    def expire_overdue_proposals(self) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            checked_at = datetime.now(timezone.utc)
            rows = connection.execute(
                """SELECT p.payload AS proposal, c.payload AS contract
                   FROM supervision_proposals AS p
                   JOIN supervision_contracts AS c ON c.id = p.original_contract_id
                   WHERE p.status IN (?, ?, ?)""",
                AWAITING_RESPONSE_STATUSES,
            ).fetchall()
            for row in rows:
                proposal = SupervisionProposal.model_validate_json(row["proposal"])
                if checked_at >= proposal.response_deadline:
                    contract = SupervisionContract.model_validate_json(row["contract"])
                    self._expire_proposal(connection, proposal, contract, checked_at)

    def _expire_proposal(
        self,
        connection: sqlite3.Connection,
        proposal: SupervisionProposal,
        contract: SupervisionContract,
        checked_at: datetime,
    ) -> None:
        proposal.status = ProposalStatus.EXPIRED
        self._save_proposal(connection, proposal)
        self._close_pending_approvals(connection, proposal.id, DecisionStatus.EXPIRED)
        self._insert_audit_event(
            connection, contract.family_id, "system", ActorType.SYSTEM,
            "PROPOSAL_EXPIRED", proposal.id, proposal.id, checked_at,
        )
        if (
            contract.status == ContractStatus.CONFLICTED
            and contract.version == proposal.original_contract_version
        ):
            self._escalate_failed_proposal(connection, contract, proposal, PROPOSAL_EXPIRY_REASON)

    def escalate_overdue_handovers(self) -> None:
        checked_at = datetime.now(timezone.utc)
        with self._connection() as connection:
            candidates = connection.execute(
                """SELECT id, start_time FROM supervision_contracts
                   WHERE status = ?""",
                (ContractStatus.CONFIRMED,),
            ).fetchall()

        for candidate in candidates:
            # Stored timestamps can have different UTC offsets.
            if datetime.fromisoformat(candidate["start_time"]) > checked_at:
                continue
            try:
                self.check_handover_timeout(
                    candidate["id"],
                    correlation_id=f"handover-timeout-{uuid4().hex}",
                )
            except (WorkflowNotFound, WorkflowConflict):
                # The contract may have changed since the candidate scan.
                continue

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
                ContractStatus.DECLINED,
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

            self._insert_audit_event(
                connection, contract.family_id, "system", ActorType.SYSTEM,
                "HANDOVER_TIMEOUT_ESCALATED", contract.id, correlation_id, checked_at,
            )
            self._insert_guardian_notification(
                connection,
                contract,
                GuardianNotificationKind.MISSED_HANDOVER,
                "The scheduled handover has no confirmation. Please contact the assigned caregiver.",
            )
        return contract

    def _insert_guardian_notification(
        self,
        connection: sqlite3.Connection,
        contract: SupervisionContract,
        kind: GuardianNotificationKind,
        message: str,
        *,
        proposal_id: Optional[str] = None,
    ) -> None:
        # Prefer the failed proposal's guardian, then the contract's guardian.
        guardian = connection.execute(
            """SELECT m.id FROM family_members AS m
               WHERE m.family_id = ? AND m.role = 'GUARDIAN'
                 AND COALESCE(json_extract(m.payload, '$.status'), 'ACTIVE') = 'ACTIVE'
               ORDER BY EXISTS (
                   SELECT 1 FROM approvals AS a
                   WHERE a.proposal_id = ? AND a.approver_id = m.id
                     AND a.approval_type = 'GUARDIAN'
               ) DESC, (m.id = ?) DESC, EXISTS (
                   SELECT 1 FROM audit_events AS e
                   JOIN approvals AS a ON a.proposal_id = e.correlation_id
                   WHERE e.resource_id = ?
                     AND json_extract(e.payload, '$.action') = 'REPLACEMENT_CONFIRMED'
                     AND a.approver_id = m.id AND a.approval_type = 'GUARDIAN'
                     AND a.status = 'APPROVED'
               ) DESC, m.id
               LIMIT 1""",
            (contract.family_id, proposal_id, contract.guardian_id, contract.id),
        ).fetchone()
        if guardian is None:
            return
        connection.execute(
            """INSERT INTO guardian_notifications
               (id, guardian_id, contract_id, kind, message, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                f"notification-{uuid4().hex}",
                guardian["id"],
                contract.id,
                kind,
                message,
                contract.updated_at.isoformat(),
            ),
        )

    def list_guardian_notifications(self, guardian_id: str) -> list[GuardianNotification]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT n.* FROM guardian_notifications AS n
                   JOIN supervision_contracts AS c ON c.id = n.contract_id
                   JOIN family_members AS m ON m.id = n.guardian_id AND m.family_id = c.family_id
                   WHERE n.guardian_id = ? AND m.role = 'GUARDIAN'
                     AND COALESCE(json_extract(m.payload, '$.status'), 'ACTIVE') = 'ACTIVE'
                   ORDER BY n.created_at DESC, n.id""",
                (guardian_id,),
            ).fetchall()
        return [GuardianNotification.model_validate(dict(row)) for row in rows]

    def _insert_audit_event(
        self,
        connection: sqlite3.Connection,
        family_id: str,
        actor_id: str,
        actor_type: ActorType,
        action: str,
        resource_id: str,
        correlation_id: str,
        timestamp: datetime,
    ) -> None:
        event = AuditEvent(
            id=f"audit-{uuid4().hex}",
            family_id=family_id,
            actor_id=actor_id,
            actor_type=actor_type,
            action=action,
            resource_id=resource_id,
            correlation_id=correlation_id,
            timestamp=timestamp,
        )
        connection.execute(
            """INSERT INTO audit_events
               (id, family_id, actor_id, resource_id, timestamp, correlation_id, payload)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                event.id, event.family_id, event.actor_id, event.resource_id,
                event.timestamp.isoformat(), event.correlation_id, event.model_dump_json(),
            ),
        )

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
                ContractStatus.DECLINED,
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
                    member,
                    contract.start_time,
                    contract.end_time,
                ):
                    candidates.append(member)

            contract.version += 1
            contract.updated_at = datetime.now(timezone.utc)
            proposal = None
            if candidates and guardians and contract.updated_at < contract.start_time:
                contract.status = ContractStatus.CONFLICTED
                guardian = next((member for member in guardians if member.id == contract.guardian_id), guardians[0])
                proposal = SupervisionProposal(
                    id=f"proposal-{uuid4().hex}",
                    original_contract_id=contract.id,
                    original_contract_version=contract.version,
                    proposed_caregiver_id=candidates[0].id,
                    proposed_start_time=contract.start_time,
                    proposed_end_time=contract.end_time,
                    expires_at=contract.start_time,
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
                        approver_id=guardian.id,
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
                if contract.updated_at >= contract.start_time:
                    message = "The supervision window has started; guardian action is required."

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
            self._insert_audit_event(
                connection, contract.family_id, event.caregiver_id, ActorType.USER,
                "CAREGIVER_CANCELLATION_RECORDED", contract.id, event.id, contract.updated_at,
            )
            if proposal is None:
                self._insert_audit_event(
                    connection, contract.family_id, "system", ActorType.SYSTEM,
                    "SUPERVISION_ESCALATED", contract.id, event.id, contract.updated_at,
                )
                self._insert_guardian_notification(
                    connection,
                    contract,
                    GuardianNotificationKind.UNRESOLVED_CANCELLATION,
                    "The assigned caregiver cancelled and no replacement was arranged. "
                    "Please arrange supervision for the scheduled window.",
                )
            else:
                self._insert_audit_event(
                    connection, contract.family_id, "system", ActorType.SYSTEM,
                    "PROPOSAL_CREATED", proposal.id, event.id, proposal.created_at,
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
