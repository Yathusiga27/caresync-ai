"""SQLite-backed repository for CareSync's initial family and contract data."""

from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
from typing import Iterator, Optional

from backend.models.supervision import (
    Family,
    FamilyMember,
    SupervisionContract,
    SupervisionProposal,
)


DEFAULT_DATABASE_PATH = Path(__file__).parent / "data" / "caresync.db"


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
                """
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

    def create_contract(self, contract: SupervisionContract) -> None:
        with self._connection() as connection:
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

    def get_contract(self, contract_id: str) -> Optional[SupervisionContract]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload FROM supervision_contracts WHERE id = ?", (contract_id,)
            ).fetchone()
        return SupervisionContract.model_validate_json(row["payload"]) if row else None

    def save_contract(self, contract: SupervisionContract) -> bool:
        with self._connection() as connection:
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

    def list_proposals(self, contract_id: str) -> list[SupervisionProposal]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT payload FROM supervision_proposals
                   WHERE original_contract_id = ? ORDER BY id""",
                (contract_id,),
            ).fetchall()
        return [SupervisionProposal.model_validate_json(row["payload"]) for row in rows]


store = SQLiteStore()
