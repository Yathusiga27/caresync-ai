import os
import tempfile
import unittest

from fastapi.testclient import TestClient

# Keep test data isolated from the local development database.
_test_database_directory = tempfile.TemporaryDirectory()
os.environ["CARESYNC_DATABASE_PATH"] = os.path.join(
    _test_database_directory.name, "test.db"
)
os.environ["CARESYNC_ENV"] = "test"

from backend.main import app
from backend.store import SQLiteStore, store


class HealthEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_health_returns_ok(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_family_endpoints_return_fictional_fixture(self) -> None:
        family = self.client.get("/families/family-demo")
        members = self.client.get("/families/family-demo/members")

        self.assertEqual(family.status_code, 200)
        self.assertEqual(family.json()["name"], "Demo Family")
        self.assertEqual(members.status_code, 200)
        self.assertEqual({member["role"] for member in members.json()}, {
            "GUARDIAN", "CAREGIVER", "CHILD"
        })

    def test_contract_is_created_pending_approval_and_can_be_cancelled(self) -> None:
        response = self.client.post(
            "/supervision/contracts",
            json={
                "family_id": "family-demo",
                "child_id": "child-demo",
                "caregiver_id": "caregiver-demo",
                "activity": "After-school care",
                "expected_location": "Home",
                "start_time": "2026-11-16T14:00:00+05:30",
                "end_time": "2026-11-16T18:00:00+05:30",
            },
        )

        self.assertEqual(response.status_code, 201)
        contract = response.json()
        self.assertEqual(contract["status"], "PENDING_APPROVAL")
        self.assertEqual(contract["version"], 1)

        cancelled = self.client.post(
            f"/supervision/contracts/{contract['id']}/cancel"
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["status"], "CANCELLED")
        self.assertEqual(cancelled.json()["version"], 2)

        reopened_store = SQLiteStore(
            database_path=store.database_path,
            environment="production",
        )
        persisted = reopened_store.get_contract(contract["id"])
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted.status, "CANCELLED")
        self.assertEqual(persisted.version, 2)

    def test_family_and_members_are_persisted(self) -> None:
        reopened_store = SQLiteStore(
            database_path=store.database_path,
            environment="production",
        )

        self.assertEqual(reopened_store.get_family("family-demo").name, "Demo Family")
        self.assertEqual(len(reopened_store.list_family_members("family-demo")), 3)

    def test_contract_rejects_naive_timestamps(self) -> None:
        response = self.client.post(
            "/supervision/contracts",
            json={
                "family_id": "family-demo",
                "child_id": "child-demo",
                "caregiver_id": "caregiver-demo",
                "activity": "After-school care",
                "expected_location": "Home",
                "start_time": "2026-11-16T14:00:00",
                "end_time": "2026-11-16T18:00:00+05:30",
            },
        )

        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
