import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

# Keep test data isolated from the local development database.
_test_database_directory = tempfile.TemporaryDirectory()
os.environ["CARESYNC_DATABASE_PATH"] = os.path.join(
    _test_database_directory.name, "test.db"
)
os.environ["CARESYNC_ENV"] = "test"

from backend.agent.agent import AgentUnavailableError
from backend.main import app
from backend.models.supervision import ContractStatus, DecisionStatus, SupervisionContract
from backend.schemas import AgentChatResponse
from backend.store import SQLiteStore, WorkflowConflict, store

FUTURE_SCHEDULE_YEAR = datetime.now(timezone.utc).year + 1


class HealthEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_health_returns_ok(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_agent_chat_endpoint_uses_agent_and_returns_model(self) -> None:
        with patch("backend.main.agent") as mock_agent:
            mock_agent.model = "qwen3:4b"
            mock_agent.respond.return_value = AgentChatResponse(
                reply="I can help review the schedule.", model="qwen3:4b"
            )
            response = self.client.post(
                "/agent/chat",
                json={"message": "Can you help me review the schedule?"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "reply": "I can help review the schedule.",
                "model": "qwen3:4b",
                "proposal": None,
                "proposal_validation_error": None,
            },
        )

    def test_agent_chat_returns_service_unavailable_when_ollama_is_offline(self) -> None:
        with patch("backend.main.agent") as mock_agent:
            mock_agent.respond.side_effect = AgentUnavailableError(
                "Cannot connect to Ollama."
            )
            response = self.client.post("/agent/chat", json={"message": "Hello"})

        self.assertEqual(response.status_code, 503)
        self.assertIn("Cannot connect to Ollama", response.json()["detail"])

    def test_agent_chat_rejects_blank_message(self) -> None:
        response = self.client.post("/agent/chat", json={"message": "   "})

        self.assertEqual(response.status_code, 422)

    def test_family_endpoints_return_fictional_fixture(self) -> None:
        family = self.client.get("/families/family-demo")
        members = self.client.get("/families/family-demo/members")

        self.assertEqual(family.status_code, 200)
        self.assertEqual(family.json()["name"], "Demo Family")
        self.assertEqual(members.status_code, 200)
        self.assertEqual({member["role"] for member in members.json()}, {
            "GUARDIAN", "CAREGIVER", "CHILD"
        })
        self.assertEqual(len(members.json()), 4)

    def test_new_family_members_can_be_used_for_scheduling(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "members.db")
            database = SQLiteStore(path, environment="test")
            with patch("backend.main.store", database):
                endpoint = "/families/family-demo/members"
                caregiver = self.client.post(endpoint, json={"name": "  Avery  ", "role": "CAREGIVER"})
                child = self.client.post(endpoint, json={"name": "Casey", "role": "CHILD"})
                self.assertEqual(caregiver.status_code, 201, caregiver.text)
                self.assertEqual(child.status_code, 201, child.text)
                self.assertEqual(caregiver.json()["name"], "Avery")
                self.assertEqual(caregiver.json()["status"], "ACTIVE")
                self.assertIn(caregiver.json(), self.client.get(endpoint).json())

                contract = self.client.post("/supervision/contracts", json={
                    "family_id": "family-demo", "child_id": child.json()["id"],
                    "caregiver_id": caregiver.json()["id"], "activity": "School pickup",
                    "expected_location": "School entrance", "start_time": "2027-01-10T14:00:00+05:30",
                    "end_time": "2027-01-10T15:00:00+05:30",
                })
                self.assertEqual(contract.status_code, 201, contract.text)
                self.assertEqual(contract.json()["caregiver_id"], caregiver.json()["id"])

            reopened = SQLiteStore(path, environment="production")
            self.assertEqual(reopened.get_member(caregiver.json()["id"]).role, "CAREGIVER")

    def test_create_family_member_validates_family_name_and_role(self) -> None:
        endpoint = "/families/family-demo/members"
        for payload in ({"name": "  ", "role": "CAREGIVER"}, {"name": "Avery", "role": "UNKNOWN"}):
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post(endpoint, json=payload).status_code, 422)
        payload = {"name": "Avery", "role": "GUARDIAN"}
        self.assertEqual(self.client.post("/families/unknown/members", json=payload).status_code, 404)
        with patch.object(store, "environment", "production"):
            self.assertEqual(self.client.post(endpoint, json=payload).status_code, 501)

    def test_unavailable_caregiver_cannot_be_assigned_but_can_confirm_existing_handover(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "scheduling-availability.db")
            database = SQLiteStore(path, environment="test")
            existing = self._make_handover_contract(database=database)
            url = "/families/family-demo/members/caregiver-demo/scheduling-availability"
            disabled = {"actor_id": "caregiver-demo", "available_for_scheduling": False}
            contract_payload = {
                "family_id": "family-demo", "child_id": "child-demo",
                "caregiver_id": "caregiver-demo", "activity": "School pickup",
                "expected_location": "School entrance", "start_time": "2027-01-10T14:00:00+05:30",
                "end_time": "2027-01-10T15:00:00+05:30",
            }

            with patch("backend.main.store", database):
                self.assertTrue(database.get_member("caregiver-demo").available_for_scheduling)
                for other_actor in ("guardian-demo", "caregiver-alt-demo"):
                    unauthorized = self.client.patch(url, json={**disabled, "actor_id": other_actor})
                    self.assertEqual(unauthorized.status_code, 403, unauthorized.text)
                self.assertTrue(database.get_member("caregiver-demo").available_for_scheduling)
                self.assertEqual(
                    self.client.patch(url, json={"available_for_scheduling": False}).status_code, 422
                )
                updated = self.client.patch(url, json=disabled)
                self.assertEqual(updated.status_code, 200, updated.text)
                self.assertFalse(updated.json()["available_for_scheduling"])
                self.assertEqual(updated.json()["status"], "ACTIVE")
                reopened = SQLiteStore(path, environment="production")
                self.assertFalse(reopened.get_member("caregiver-demo").available_for_scheduling)
                rejected = self.client.post("/supervision/contracts", json=contract_payload)
                self.assertEqual(rejected.status_code, 409, rejected.text)

                handover = self.client.post(
                    f"/supervision/contracts/{existing.id}/confirm-handover",
                    json={"actor_id": "caregiver-demo"},
                )
                self.assertEqual(handover.status_code, 200, handover.text)
                self.assertEqual(handover.json()["status"], "ACTIVE")

                enabled = self.client.patch(url, json={**disabled, "available_for_scheduling": True})
                self.assertEqual(enabled.status_code, 200, enabled.text)
                self.assertEqual(self.client.post("/supervision/contracts", json=contract_payload).status_code, 201)
                self.assertEqual(
                    self.client.patch(url, json={**disabled, "available_for_scheduling": "false"}).status_code,
                    422,
                )
                self.assertEqual(self.client.patch(url.replace("family-demo", "family-other"), json=disabled).status_code, 404)
                self.assertEqual(
                    self.client.patch(url.replace("caregiver-demo", "child-demo"),
                                      json={**disabled, "actor_id": "child-demo"}).status_code,
                    409,
                )

    def test_unavailable_caregiver_is_excluded_from_replacements(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = SQLiteStore(os.path.join(directory, "replacement-availability.db"), environment="test")
            with patch("backend.main.store", database):
                contract, proposal = self._make_proposal("08-22", "before-availability-toggle")
                approval = self._approval_for(proposal["id"], "GUARDIAN")
                updated = self.client.patch(
                    "/families/family-demo/members/caregiver-alt-demo/scheduling-availability",
                    json={"actor_id": "caregiver-alt-demo", "available_for_scheduling": False},
                )
                self.assertEqual(updated.status_code, 200, updated.text)
                self.assertEqual(database.list_available_caregivers_for_contract(contract["id"]), [])
                self.assertFalse(database.validate_replacement_candidate(
                    contract["id"], "caregiver-alt-demo", proposal["original_contract_version"]
                )["valid"])

                second = self._create_contract(contract["start_time"], contract["end_time"])
                outcome = self._submit_cancellation(second["id"], "after-availability-toggle")
                self.assertEqual(outcome["contract_status"], "ESCALATED")
                self.assertIsNone(outcome["proposal"])

                approved = self.client.post(
                    f"/approvals/{approval['id']}/approve", json={"actor_id": "guardian-demo"},
                )
                self.assertEqual(approved.status_code, 200, approved.text)
                acceptance = self.client.post(
                    f"/proposals/{proposal['id']}/accept", json={"actor_id": "caregiver-alt-demo"},
                )
                self.assertEqual(acceptance.status_code, 409, acceptance.text)
                self.assertEqual(database.list_proposals(contract["id"])[0].status, "INVALIDATED")

    def _create_contract(self, start_time: str, end_time: str) -> dict:
        response = self.client.post(
            "/supervision/contracts",
            json={
                "family_id": "family-demo",
                "child_id": "child-demo",
                "caregiver_id": "caregiver-demo",
                "activity": "After-school care",
                "expected_location": "Home",
                "start_time": start_time,
                "end_time": end_time,
            },
        )
        self.assertEqual(response.status_code, 201)
        return response.json()

    def _submit_cancellation(self, contract_id: str, event_id: str) -> dict:
        response = self.client.post(
            "/agent/events",
            json={
                "id": event_id,
                "contract_id": contract_id,
                "caregiver_id": "caregiver-demo",
                "occurred_at": "2026-11-12T10:00:00+05:30",
                "reason": "Unexpected appointment",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _approval_for(self, proposal_id: str, approval_type: str) -> dict:
        response = self.client.get("/approvals/pending")
        self.assertEqual(response.status_code, 200, response.text)
        matches = [
            item
            for item in response.json()
            if item["proposal_id"] == proposal_id
            and item["approval_type"] == approval_type
        ]
        self.assertEqual(len(matches), 1)
        return matches[0]

    def _make_proposal(self, day: str, event_id: str) -> tuple[dict, dict]:
        schedule_day = f"{FUTURE_SCHEDULE_YEAR}-{day}"
        contract = self._create_contract(
            f"{schedule_day}T14:00:00+05:30", f"{schedule_day}T18:00:00+05:30"
        )
        availability = self.client.post(
            "/caregivers/caregiver-alt-demo/availability",
            json={
                "start_time": f"{schedule_day}T13:00:00+05:30",
                "end_time": f"{schedule_day}T19:00:00+05:30",
                "availability_status": "AVAILABLE",
                "source": "CAREGIVER",
            },
        )
        self.assertEqual(availability.status_code, 201, availability.text)
        outcome = self._submit_cancellation(contract["id"], event_id)
        return contract, outcome["proposal"]

    def _make_handover_contract(
        self,
        status: ContractStatus = ContractStatus.CONFIRMED,
        start_offset: timedelta = timedelta(minutes=-10),
        end_offset: timedelta = timedelta(minutes=10),
        *,
        database: SQLiteStore = store,
        time_zone: timezone = timezone.utc,
    ) -> SupervisionContract:
        now = datetime.now(time_zone)
        contract = SupervisionContract(
            id=f"contract-handover-{uuid4().hex}",
            family_id="family-demo",
            child_id="child-demo",
            caregiver_id="caregiver-demo",
            activity="Test handover",
            expected_location="Home",
            start_time=now + start_offset,
            end_time=now + end_offset,
            status=status,
            approval_status=DecisionStatus.APPROVED,
            acceptance_status=DecisionStatus.ACCEPTED,
        )
        database.create_contract(contract)
        return contract

    def test_scheduler_escalates_overdue_pending_handover_only_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = SQLiteStore(os.path.join(directory, "scheduler.db"), environment="test")
            overdue = self._make_handover_contract(
                database=database, time_zone=timezone(timedelta(hours=5, minutes=30))
            )
            future = self._make_handover_contract(
                start_offset=timedelta(hours=1), end_offset=timedelta(hours=2),
                database=database, time_zone=timezone(timedelta(hours=-5)),
            )
            active = self._make_handover_contract(database=database)
            database.confirm_handover(active.id, active.caregiver_id, "test-handover")

            database.escalate_overdue_handovers()
            database.escalate_overdue_handovers()

            self.assertEqual(database.get_contract(overdue.id).status, "ESCALATED")
            self.assertEqual(database.get_contract(overdue.id).version, 2)
            inbox = database.list_guardian_notifications("guardian-demo")
            self.assertEqual([notice.contract_id for notice in inbox], [overdue.id])
            self.assertEqual(
                [event.action for event in database.list_audit_events(overdue.id)],
                ["HANDOVER_TIMEOUT_ESCALATED"],
            )
            self.assertEqual(database.get_contract(future.id).status, "CONFIRMED")
            self.assertEqual(database.list_audit_events(future.id), [])
            self.assertEqual(database.get_contract(active.id).status, "ACTIVE")
            self.assertEqual(
                [event.action for event in database.list_audit_events(active.id)],
                ["HANDOVER_CONFIRMED"],
            )

    def test_scheduler_and_caregiver_confirmation_record_only_one_transition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = SQLiteStore(os.path.join(directory, "scheduler.db"), environment="test")
            contract = self._make_handover_contract(database=database)
            start = Barrier(2, timeout=5)

            def sweep() -> None:
                start.wait()
                database.escalate_overdue_handovers()

            def confirm() -> bool:
                start.wait()
                try:
                    database.confirm_handover(contract.id, contract.caregiver_id, "race")
                except WorkflowConflict:
                    return False
                return True

            with ThreadPoolExecutor(max_workers=2) as executor:
                sweep_result = executor.submit(sweep)
                confirmation_result = executor.submit(confirm)
                sweep_result.result()
                confirmed = confirmation_result.result()

            current = database.get_contract(contract.id)
            self.assertEqual(current.version, 2)
            self.assertEqual(current.status, "ACTIVE" if confirmed else "ESCALATED")
            expected_action = "HANDOVER_CONFIRMED" if confirmed else "HANDOVER_TIMEOUT_ESCALATED"
            self.assertEqual(
                [event.action for event in database.list_audit_events(contract.id)],
                [expected_action],
            )

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
        original_version = reopened_store.get_contract_version(contract["id"], 1)
        self.assertIsNotNone(original_version)
        self.assertEqual(original_version.status, "PENDING_APPROVAL")

    def test_family_schedule_lists_contracts_in_chronological_order(self) -> None:
        later = self._create_contract("2027-01-05T08:00:00Z", "2027-01-05T09:00:00Z")
        earlier = self._create_contract(
            "2027-01-05T11:00:00+05:30", "2027-01-05T12:00:00+05:30"
        )
        endpoint = "/families/family-demo/contracts"
        response = self.client.get(endpoint)
        self.assertEqual(response.status_code, 200, response.text)
        contracts = response.json()
        self.assertTrue(all(contract["family_id"] == "family-demo" for contract in contracts))
        added = [contract for contract in contracts if contract["id"] in {earlier["id"], later["id"]}]
        self.assertEqual(added, [earlier, later])
        self.assertEqual(self.client.get("/families/unknown/contracts").status_code, 404)
        with patch.object(store, "environment", "production"):
            self.assertEqual(self.client.get(endpoint).status_code, 501)

    def test_initial_schedule_confirms_when_the_assigned_caregiver_accepts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = SQLiteStore(os.path.join(directory, "initial-schedule.db"), environment="test")
            with patch("backend.main.store", database):
                guardian = self.client.post("/families/family-demo/members", json={
                    "name": "Avery", "role": "GUARDIAN",
                }).json()
                now = datetime.now(timezone.utc)
                request = self.client.post("/supervision/contracts", json={
                    "family_id": "family-demo", "child_id": "child-demo",
                    "guardian_id": guardian["id"], "caregiver_id": "caregiver-alt-demo",
                    "activity": "School pickup", "expected_location": "School",
                    "start_time": (now - timedelta(minutes=5)).isoformat(),
                    "end_time": (now + timedelta(minutes=30)).isoformat(),
                })
                self.assertEqual(request.status_code, 201, request.text)
                contract = request.json()
                self.assertEqual(contract["approval_status"], "APPROVED")
                endpoint = f"/supervision/contracts/{contract['id']}/schedule-decisions"
                queue = "/families/family-demo/schedule-requests"

                guardian_queue = self.client.get(queue, params={"actor_id": guardian["id"]}).json()
                caregiver_queue = self.client.get(queue, params={"actor_id": "caregiver-alt-demo"}).json()
                self.assertEqual(guardian_queue, [])
                self.assertEqual(caregiver_queue[0]["approval_type"], "CAREGIVER")
                self.assertEqual(self.client.get(queue, params={"actor_id": "guardian-demo"}).json(), [])
                wrong_guardian = self.client.post(endpoint, json={
                    "actor_id": guardian["id"], "decision": "ACCEPTED",
                })
                self.assertEqual(wrong_guardian.status_code, 409)

                accepted = self.client.post(endpoint, json={
                    "actor_id": "caregiver-alt-demo", "decision": "ACCEPTED",
                    "expected_version": contract["version"],
                })
                self.assertEqual(accepted.status_code, 200, accepted.text)
                self.assertEqual(accepted.json()["status"], "CONFIRMED")
                self.assertEqual(accepted.json()["id"], contract["id"])
                self.assertEqual(self.client.get(queue, params={"actor_id": "caregiver-alt-demo"}).json(), [])

                handover = self.client.post(
                    f"/supervision/contracts/{contract['id']}/confirm-handover",
                    json={"actor_id": "caregiver-alt-demo"},
                )
                self.assertEqual(handover.status_code, 200, handover.text)
                self.assertEqual(handover.json()["status"], "ACTIVE")
                self.assertCountEqual(
                    [event.action for event in database.list_audit_events(contract["id"])],
                    ["SCHEDULE_CAREGIVER_ACCEPTED", "SCHEDULE_CONFIRMED", "HANDOVER_CONFIRMED"],
                )

    def test_declined_schedule_can_be_requested_again_by_its_guardian(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = SQLiteStore(os.path.join(directory, "declined-schedule.db"), environment="test")
            with patch("backend.main.store", database):
                start = datetime.now(timezone.utc) + timedelta(days=1)
                contract = self._create_contract(start.isoformat(), (start + timedelta(hours=1)).isoformat())
                detail = f"/supervision/contracts/{contract['id']}"
                queue = "/families/family-demo/schedule-requests"
                declined = self.client.post(f"{detail}/schedule-decisions", json={
                    "actor_id": "caregiver-demo", "decision": "DECLINED", "reason": "  Away that afternoon.  ",
                })
                self.assertEqual(declined.status_code, 200, declined.text)
                declined = declined.json()
                self.assertEqual(declined["status"], "DECLINED")
                self.assertEqual(declined["decline_reason"], "Away that afternoon.")
                self.assertEqual(self.client.get(queue, params={"actor_id": "caregiver-demo"}).json(), [])
                request = {"actor_id": "guardian-demo", "caregiver_id": "caregiver-alt-demo",
                           "expected_version": declined["version"]}
                path = f"{detail}/request-caregiver"
                for invalid in ({"actor_id": "caregiver-demo"}, {"caregiver_id": "caregiver-demo"},
                                {"expected_version": contract["version"]}):
                    self.assertEqual(self.client.post(path, json={**request, **invalid}).status_code, 409)
                resent = self.client.post(path, json=request)
                self.assertEqual(resent.status_code, 200, resent.text)
                resent = resent.json()
                self.assertEqual(resent["id"], contract["id"])
                self.assertEqual(resent["start_time"], contract["start_time"])
                self.assertEqual(resent["caregiver_id"], "caregiver-alt-demo")
                self.assertEqual(resent["acceptance_status"], "PENDING")
                self.assertIsNone(resent["decline_reason"])
                previous = database.get_contract_version(contract["id"], declined["version"])
                self.assertEqual(previous.decline_reason, "Away that afternoon.")
                self.assertEqual(self.client.post(path, json=request).status_code, 409)
                pending = self.client.get(queue, params={"actor_id": "caregiver-alt-demo"}).json()
                self.assertEqual([item["contract"]["id"] for item in pending], [contract["id"]])
                response_path = f"{detail}/schedule-decisions"
                self.assertEqual(self.client.post(response_path, json={
                    "actor_id": "caregiver-demo", "decision": "ACCEPTED",
                }).status_code, 409)
                self.assertEqual(self.client.post(response_path, json={
                    "actor_id": "caregiver-alt-demo", "decision": "ACCEPTED",
                    "expected_version": declined["version"],
                }).status_code, 409)
                second_decline = self.client.post(response_path, json={
                    "actor_id": "caregiver-alt-demo", "decision": "DECLINED",
                    "expected_version": resent["version"],
                })
                self.assertEqual(second_decline.status_code, 200, second_decline.text)
                self.assertIsNone(second_decline.json()["decline_reason"])

    def test_legacy_schedule_waiting_for_guardian_confirmation_is_migrated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "legacy-schedule.db")
            database = SQLiteStore(path, environment="test")
            with patch("backend.main.store", database):
                start = datetime.now(timezone.utc) + timedelta(days=1)
                contract = self._create_contract(start.isoformat(), (start + timedelta(hours=1)).isoformat())
                with closing(sqlite3.connect(path)) as connection, connection:
                    connection.execute(
                        """UPDATE supervision_contracts
                           SET payload = json_set(json_remove(payload, '$.guardian_id'),
                               '$.approval_status', 'PENDING', '$.acceptance_status', 'ACCEPTED')
                           WHERE id = ?""",
                        (contract["id"],),
                    )
                reopened = SQLiteStore(path, environment="test")
                migrated = reopened.get_contract(contract["id"])
                self.assertEqual(migrated.status, "CONFIRMED")
                self.assertEqual(migrated.guardian_id, "guardian-demo")
                self.assertEqual(migrated.version, contract["version"] + 1)
                reopened_again = SQLiteStore(path, environment="test")
                self.assertEqual(reopened_again.get_contract(contract["id"]), migrated)

    def test_family_and_members_are_persisted(self) -> None:
        reopened_store = SQLiteStore(
            database_path=store.database_path,
            environment="production",
        )

        self.assertEqual(reopened_store.get_family("family-demo").name, "Demo Family")
        self.assertEqual(len(reopened_store.list_family_members("family-demo")), 4)

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

    def test_caregiver_availability_persists(self) -> None:
        response = self.client.post(
            "/caregivers/caregiver-alt-demo/availability",
            json={
                "start_time": "2027-04-10T13:00:00+05:30",
                "end_time": "2027-04-10T19:00:00+05:30",
                "availability_status": "AVAILABLE",
                "source": "CAREGIVER",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)

        reopened_store = SQLiteStore(
            database_path=store.database_path,
            environment="production",
        )
        records = reopened_store.list_caregiver_availability("caregiver-alt-demo")
        self.assertIn(response.json()["id"], {record.id for record in records})

    def test_cancellation_proposes_caregiver_available_for_full_window(self) -> None:
        day = f"{FUTURE_SCHEDULE_YEAR}-01-10"
        contract = self._create_contract(
            f"{day}T14:00:00+05:30", f"{day}T18:00:00+05:30"
        )
        availability = self.client.post(
            "/caregivers/caregiver-alt-demo/availability",
            json={
                "start_time": f"{day}T13:00:00+05:30",
                "end_time": f"{day}T19:00:00+05:30",
                "availability_status": "AVAILABLE",
                "source": "CAREGIVER",
            },
        )
        self.assertEqual(availability.status_code, 201, availability.text)

        outcome = self._submit_cancellation(contract["id"], "event-available-001")

        self.assertEqual(outcome["contract_status"], "CONFLICTED")
        self.assertEqual(outcome["contract_version"], 2)
        self.assertEqual(outcome["proposal"]["proposed_caregiver_id"], "caregiver-alt-demo")
        self.assertEqual(outcome["proposal"]["original_contract_version"], 2)
        previous_version = store.get_contract_version(contract["id"], 1)
        self.assertIsNotNone(previous_version)
        self.assertEqual(previous_version.status, "PENDING_APPROVAL")
        proposals = self.client.get(
            f"/supervision/contracts/{contract['id']}/proposals"
        )
        self.assertEqual(len(proposals.json()), 1)
        self.assertFalse(
            any(
                notice.contract_id == contract["id"]
                for notice in store.list_guardian_notifications("guardian-demo")
            )
        )
        cancellation_events = store.list_audit_events(contract["id"])
        self.assertEqual(
            [(event.action, event.actor_id, event.correlation_id)
             for event in cancellation_events],
            [("CAREGIVER_CANCELLATION_RECORDED", "caregiver-demo", outcome["event_id"])],
        )
        proposal_events = store.list_audit_events(outcome["proposal"]["id"])
        self.assertEqual(
            [(event.action, event.correlation_id) for event in proposal_events],
            [("PROPOSAL_CREATED", outcome["event_id"])],
        )

    def test_cancellation_escalates_when_alternative_is_unavailable(self) -> None:
        day = f"{FUTURE_SCHEDULE_YEAR}-02-10"
        contract = self._create_contract(
            f"{day}T14:00:00+05:30", f"{day}T18:00:00+05:30"
        )
        available = self.client.post(
            "/caregivers/caregiver-alt-demo/availability",
            json={
                "start_time": f"{day}T13:00:00+05:30",
                "end_time": f"{day}T19:00:00+05:30",
                "availability_status": "AVAILABLE",
                "source": "CAREGIVER",
            },
        )
        self.assertEqual(available.status_code, 201, available.text)
        unavailable = self.client.post(
            "/caregivers/caregiver-alt-demo/availability",
            json={
                "start_time": f"{day}T15:00:00+05:30",
                "end_time": f"{day}T17:00:00+05:30",
                "availability_status": "UNAVAILABLE",
                "source": "CAREGIVER",
            },
        )
        self.assertEqual(unavailable.status_code, 201, unavailable.text)

        outcome = self._submit_cancellation(contract["id"], "event-unavailable-001")

        self.assertEqual(outcome["contract_status"], "ESCALATED")
        self.assertIsNone(outcome["proposal"])
        self.assertTrue(outcome["requires_guardian_action"])

    def test_cancellation_escalates_when_no_availability_is_recorded(self) -> None:
        day = f"{FUTURE_SCHEDULE_YEAR}-03-10"
        contract = self._create_contract(
            f"{day}T14:00:00+05:30", f"{day}T18:00:00+05:30"
        )

        outcome = self._submit_cancellation(contract["id"], "event-no-alt-001")

        self.assertEqual(outcome["contract_status"], "ESCALATED")
        self.assertIsNone(outcome["proposal"])
        duplicate = self._submit_cancellation(contract["id"], "event-no-alt-001")
        self.assertTrue(duplicate["duplicate"])

        inbox = self.client.get("/guardians/guardian-demo/notifications")
        self.assertEqual(inbox.status_code, 200, inbox.text)
        notices = [notice for notice in inbox.json() if notice["contract_id"] == contract["id"]]
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["guardian_id"], "guardian-demo")
        self.assertEqual(notices[0]["kind"], "UNRESOLVED_CANCELLATION")

    def test_duplicate_cancellation_event_does_not_create_another_proposal(self) -> None:
        day = f"{FUTURE_SCHEDULE_YEAR}-05-10"
        contract = self._create_contract(
            f"{day}T14:00:00+05:30", f"{day}T18:00:00+05:30"
        )
        availability = self.client.post(
            "/caregivers/caregiver-alt-demo/availability",
            json={
                "start_time": f"{day}T13:00:00+05:30",
                "end_time": f"{day}T19:00:00+05:30",
                "availability_status": "AVAILABLE",
                "source": "CAREGIVER",
            },
        )
        self.assertEqual(availability.status_code, 201, availability.text)

        first = self._submit_cancellation(contract["id"], "event-duplicate-001")
        duplicate = self._submit_cancellation(contract["id"], "event-duplicate-001")

        self.assertFalse(first["duplicate"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(first["proposal"]["id"], duplicate["proposal"]["id"])
        proposals = self.client.get(
            f"/supervision/contracts/{contract['id']}/proposals"
        )
        self.assertEqual(len(proposals.json()), 1)

    def test_caregiver_acceptance_then_guardian_approval_confirms_contract(self) -> None:
        contract, proposal = self._make_proposal("06-10", "event-accept-first-001")
        guardian_approval = self._approval_for(proposal["id"], "GUARDIAN")

        accepted = self.client.post(
            f"/proposals/{proposal['id']}/accept",
            json={"actor_id": "caregiver-alt-demo"},
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(accepted.json()["proposal"]["status"], "ACCEPTED")
        self.assertIsNone(accepted.json()["replacement_contract"])

        approved = self.client.post(
            f"/approvals/{guardian_approval['id']}/approve",
            json={"actor_id": "guardian-demo"},
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(approved.json()["proposal"]["status"], "CONFIRMED")
        replacement = approved.json()["replacement_contract"]
        self.assertEqual(replacement["status"], "CONFIRMED")
        self.assertEqual(replacement["approval_status"], "APPROVED")
        self.assertEqual(replacement["acceptance_status"], "ACCEPTED")
        self.assertEqual(replacement["caregiver_id"], "caregiver-alt-demo")
        self.assertCountEqual(
            [(event.action, event.actor_id)
             for event in store.list_audit_events(proposal["id"])],
            [
                ("PROPOSAL_CREATED", "system"),
                ("CAREGIVER_ACCEPTED", "caregiver-alt-demo"),
                ("GUARDIAN_APPROVED", "guardian-demo"),
            ],
        )
        self.assertEqual(
            [(event.action, event.correlation_id)
             for event in store.list_audit_events(replacement["id"])],
            [("REPLACEMENT_CONFIRMED", proposal["id"])],
        )
        self.assertEqual(
            self.client.get(f"/supervision/contracts/{contract['id']}").json()["status"],
            "CONFLICTED",
        )

    def test_guardian_approval_then_caregiver_acceptance_confirms_contract(self) -> None:
        _, proposal = self._make_proposal("07-10", "event-approve-first-001")
        guardian_approval = self._approval_for(proposal["id"], "GUARDIAN")

        approved = self.client.post(
            f"/approvals/{guardian_approval['id']}/approve",
            json={"actor_id": "guardian-demo"},
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(approved.json()["proposal"]["status"], "APPROVED")
        self.assertIsNone(approved.json()["replacement_contract"])

        accepted = self.client.post(
            f"/proposals/{proposal['id']}/accept",
            json={"actor_id": "caregiver-alt-demo"},
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(accepted.json()["proposal"]["status"], "CONFIRMED")
        self.assertIsNotNone(accepted.json()["replacement_contract"])

    def test_only_assigned_guardian_can_record_guardian_response(self) -> None:
        _, proposal = self._make_proposal("07-20", "event-wrong-actor-001")
        guardian_approval = self._approval_for(proposal["id"], "GUARDIAN")

        response = self.client.post(
            f"/approvals/{guardian_approval['id']}/approve",
            json={"actor_id": "caregiver-alt-demo"},
        )

        self.assertEqual(response.status_code, 409)

    def _assert_failed_proposal_notice(self, contract_id: str, reason: str) -> None:
        inbox = self.client.get("/guardians/guardian-demo/notifications")
        self.assertEqual(inbox.status_code, 200, inbox.text)
        notices = [notice for notice in inbox.json() if notice["contract_id"] == contract_id]
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["kind"], "REPLACEMENT_PROPOSAL_FAILED")
        self.assertEqual(
            notices[0]["message"],
            f"{reason} Please arrange supervision for the scheduled window.",
        )

    def test_guardian_rejection_escalates_contract_and_notifies_guardian(self) -> None:
        contract, proposal = self._make_proposal("08-05", "event-reject-001")
        guardian_approval = self._approval_for(proposal["id"], "GUARDIAN")
        endpoint = f"/approvals/{guardian_approval['id']}/reject"
        payload = {"actor_id": "guardian-demo"}

        rejected = self.client.post(endpoint, json=payload)
        self.assertEqual(rejected.status_code, 200, rejected.text)
        self.assertEqual(rejected.json()["proposal"]["status"], "REJECTED")
        self.assertEqual(store.get_contract(contract["id"]).status, "ESCALATED")

        self.assertEqual(self.client.post(endpoint, json=payload).status_code, 409)
        self._assert_failed_proposal_notice(
            contract["id"], "The guardian rejected the replacement proposal."
        )

    def test_caregiver_decline_escalates_contract(self) -> None:
        contract, proposal = self._make_proposal("08-10", "event-decline-001")
        endpoint = f"/proposals/{proposal['id']}/decline"
        payload = {"actor_id": "caregiver-alt-demo"}

        declined = self.client.post(endpoint, json=payload)

        self.assertEqual(declined.status_code, 200, declined.text)
        self.assertEqual(declined.json()["proposal"]["status"], "DECLINED")
        self.assertIn(
            ("CAREGIVER_DECLINED", "caregiver-alt-demo"),
            [(event.action, event.actor_id)
             for event in store.list_audit_events(proposal["id"])],
        )
        self.assertIn(
            ("SUPERVISION_ESCALATED", proposal["id"]),
            [(event.action, event.correlation_id)
             for event in store.list_audit_events(contract["id"])],
        )
        self.assertEqual(
            self.client.get(f"/supervision/contracts/{contract['id']}").json()["status"],
            "ESCALATED",
        )
        self.assertEqual(self.client.post(endpoint, json=payload).status_code, 409)
        self._assert_failed_proposal_notice(
            contract["id"], "The proposed caregiver declined the replacement proposal."
        )

    def test_stale_proposal_is_invalidated_instead_of_approved(self) -> None:
        contract, proposal = self._make_proposal("09-10", "event-stale-001")
        guardian_approval = self._approval_for(proposal["id"], "GUARDIAN")
        original = store.get_contract(contract["id"])
        original.version += 1
        original.updated_at += timedelta(seconds=1)
        store.save_contract(original)

        response = self.client.post(
            f"/approvals/{guardian_approval['id']}/approve",
            json={"actor_id": "guardian-demo"},
        )

        self.assertEqual(response.status_code, 409)
        current_contract = self.client.get(
            f"/supervision/contracts/{contract['id']}"
        ).json()
        self.assertEqual(current_contract["status"], "ESCALATED")
        proposals = self.client.get(
            f"/supervision/contracts/{contract['id']}/proposals"
        ).json()
        self.assertEqual(proposals[0]["status"], "INVALIDATED")
        self._assert_failed_proposal_notice(
            contract["id"], "The original contract changed after this proposal was created."
        )

    def test_invalidated_proposal_for_cancelled_contract_does_not_send_notice(self) -> None:
        contract, proposal = self._make_proposal("09-15", "event-cancelled-original-001")
        guardian_approval = self._approval_for(proposal["id"], "GUARDIAN")
        cancelled = self.client.post(f"/supervision/contracts/{contract['id']}/cancel")
        self.assertEqual(cancelled.status_code, 200, cancelled.text)

        response = self.client.post(
            f"/approvals/{guardian_approval['id']}/approve",
            json={"actor_id": "guardian-demo"},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(store.list_proposals(contract["id"])[0].status, "INVALIDATED")
        self.assertEqual(store.get_contract(contract["id"]).status, "CANCELLED")
        notices = store.list_guardian_notifications("guardian-demo")
        self.assertFalse(any(notice.contract_id == contract["id"] for notice in notices))

    def test_availability_change_invalidates_before_confirmation(self) -> None:
        _, proposal = self._make_proposal("10-10", "event-availability-changed-001")
        guardian_approval = self._approval_for(proposal["id"], "GUARDIAN")
        approved = self.client.post(
            f"/approvals/{guardian_approval['id']}/approve",
            json={"actor_id": "guardian-demo"},
        )
        self.assertEqual(approved.status_code, 200, approved.text)

        unavailable = self.client.post(
            "/caregivers/caregiver-alt-demo/availability",
            json={
                "start_time": f"{FUTURE_SCHEDULE_YEAR}-10-10T15:00:00+05:30",
                "end_time": f"{FUTURE_SCHEDULE_YEAR}-10-10T17:00:00+05:30",
                "availability_status": "UNAVAILABLE",
                "source": "CAREGIVER",
            },
        )
        self.assertEqual(unavailable.status_code, 201, unavailable.text)

        response = self.client.post(
            f"/proposals/{proposal['id']}/accept",
            json={"actor_id": "caregiver-alt-demo"},
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("no longer available", response.json()["detail"])
        proposals = self.client.get(
            f"/supervision/contracts/{proposal['original_contract_id']}/proposals"
        ).json()
        self.assertEqual(proposals[0]["status"], "INVALIDATED")
        self.assertIsNone(response.json().get("replacement_contract"))
        self._assert_failed_proposal_notice(
            proposal["original_contract_id"],
            "The proposed caregiver is no longer available for the full interval.",
        )

    def test_proposal_sweep_expires_overdue_legacy_proposal_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "proposal-expiry.db")
            database = SQLiteStore(path, environment="test")
            with patch("backend.main.store", database):
                contract, proposal = self._make_proposal("11-10", "expiry-overdue")
                _, future = self._make_proposal("11-20", "expiry-future")
                guardian = self._approval_for(proposal["id"], "GUARDIAN")
                caregiver = self._approval_for(proposal["id"], "CAREGIVER")
                approved = self.client.post(
                    f"/approvals/{guardian['id']}/approve",
                    json={"actor_id": "guardian-demo"},
                )
                self.assertEqual(approved.status_code, 200, approved.text)
                self.assertEqual(proposal["expires_at"], proposal["proposed_start_time"])
                with closing(sqlite3.connect(path)) as connection, connection:
                    connection.execute(
                        """UPDATE supervision_proposals
                           SET payload = json_remove(payload, '$.expires_at') WHERE id = ?""",
                        (proposal["id"],),
                    )

                with patch("backend.store.datetime", wraps=datetime) as clock:
                    clock.now.return_value = datetime.fromisoformat(proposal["proposed_start_time"])
                    database.expire_overdue_proposals()
                    database.expire_overdue_proposals()

                self.assertEqual(database.list_proposals(contract["id"])[0].status, "EXPIRED")
                self.assertEqual(database.get_contract(contract["id"]).status, "ESCALATED")
                self.assertEqual(database.get_contract(contract["id"]).version, 3)
                self.assertCountEqual(
                    [event.action for event in database.list_audit_events(proposal["id"])],
                    ["PROPOSAL_CREATED", "GUARDIAN_APPROVED", "PROPOSAL_EXPIRED"],
                )
                pending = {approval.id for approval in database.list_pending_approvals()}
                self.assertNotIn(caregiver["id"], pending)
                self.assertIn(self._approval_for(future["id"], "GUARDIAN")["id"], pending)
                self._assert_failed_proposal_notice(
                    contract["id"], "The replacement proposal expired before all required responses arrived."
                )

    def test_late_guardian_approval_expires_proposal_without_scheduler(self) -> None:
        contract, proposal = self._make_proposal("11-25", "expiry-late-approval")
        guardian = self._approval_for(proposal["id"], "GUARDIAN")
        accepted = self.client.post(
            f"/proposals/{proposal['id']}/accept",
            json={"actor_id": "caregiver-alt-demo"},
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        endpoint = f"/approvals/{guardian['id']}/approve"
        payload = {"actor_id": "guardian-demo"}

        with patch("backend.store.datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime.fromisoformat(proposal["proposed_start_time"])
            response = self.client.post(endpoint, json=payload)
            self.assertEqual(response.status_code, 409, response.text)
            self.assertIn("expired", response.json()["detail"])
            repeated = self.client.post(endpoint, json=payload)
            self.assertEqual(repeated.status_code, 409, repeated.text)
            self.assertEqual(repeated.json(), response.json())

        self.assertEqual(store.list_proposals(contract["id"])[0].status, "EXPIRED")
        self.assertEqual(store.get_contract(contract["id"]).status, "ESCALATED")
        events = store.list_audit_events(proposal["id"])
        self.assertEqual([event.action for event in events].count("PROPOSAL_EXPIRED"), 1)
        self.assertNotIn("GUARDIAN_APPROVED", [event.action for event in events])
        self._assert_failed_proposal_notice(
            contract["id"], "The replacement proposal expired before all required responses arrived."
        )

    def test_assigned_caregiver_confirms_handover_and_contract_becomes_active(self) -> None:
        contract = self._make_handover_contract()
        endpoint = f"/supervision/contracts/{contract.id}/confirm-handover"

        response = self.client.post(endpoint, json={"actor_id": "caregiver-demo"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "ACTIVE")
        self.assertEqual(response.json()["handover_status"], "CONFIRMED")
        self.assertEqual(response.json()["version"], 2)

        reopened_store = SQLiteStore(
            database_path=store.database_path,
            environment="production",
        )
        persisted = reopened_store.get_contract(contract.id)
        self.assertEqual(persisted.status, "ACTIVE")
        audit_events = reopened_store.list_audit_events(contract.id)
        self.assertEqual(len(audit_events), 1)
        self.assertEqual(audit_events[0].action, "HANDOVER_CONFIRMED")
        self.assertEqual(audit_events[0].actor_id, "caregiver-demo")

        repeated = self.client.post(endpoint, json={"actor_id": "caregiver-demo"})
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertEqual(repeated.json()["version"], 2)
        self.assertEqual(len(store.list_audit_events(contract.id)), 1)

    def test_only_assigned_caregiver_can_confirm_handover(self) -> None:
        contract = self._make_handover_contract()

        response = self.client.post(
            f"/supervision/contracts/{contract.id}/confirm-handover",
            json={"actor_id": "caregiver-alt-demo"},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(store.get_contract(contract.id).status, "CONFIRMED")
        self.assertEqual(store.list_audit_events(contract.id), [])

    def test_handover_rejected_before_contract_start(self) -> None:
        contract = self._make_handover_contract(
            start_offset=timedelta(hours=1),
            end_offset=timedelta(hours=2),
        )

        response = self.client.post(
            f"/supervision/contracts/{contract.id}/confirm-handover",
            json={"actor_id": "caregiver-demo"},
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("has not started", response.json()["detail"])
        self.assertEqual(store.get_contract(contract.id).status, "CONFIRMED")

    def test_handover_rejected_for_contract_not_confirmed(self) -> None:
        contract = self._make_handover_contract(status=ContractStatus.PENDING_APPROVAL)

        response = self.client.post(
            f"/supervision/contracts/{contract.id}/confirm-handover",
            json={"actor_id": "caregiver-demo"},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(store.get_contract(contract.id).status, "PENDING_APPROVAL")

    def test_missed_handover_timeout_escalates_once_and_records_audit(self) -> None:
        contract = self._make_handover_contract()
        endpoint = (
            f"/supervision/contracts/{contract.id}/check-handover-timeout"
        )

        response = self.client.post(endpoint)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "ESCALATED")
        self.assertEqual(response.json()["handover_status"], "PENDING")
        self.assertEqual(response.json()["version"], 2)
        prior = store.get_contract_version(contract.id, 1)
        self.assertIsNotNone(prior)
        self.assertEqual(prior.status, "CONFIRMED")
        events = store.list_audit_events(contract.id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].action, "HANDOVER_TIMEOUT_ESCALATED")
        self.assertEqual(events[0].actor_type, "SYSTEM")

        repeated = self.client.post(endpoint)
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertEqual(repeated.json()["version"], 2)
        self.assertEqual(len(store.list_audit_events(contract.id)), 1)

        inbox = self.client.get("/guardians/guardian-demo/notifications")
        self.assertEqual(inbox.status_code, 200, inbox.text)
        notices = [notice for notice in inbox.json() if notice["contract_id"] == contract.id]
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["guardian_id"], "guardian-demo")
        self.assertEqual(notices[0]["kind"], "MISSED_HANDOVER")
        self.assertEqual(self.client.get("/guardians/caregiver-demo/notifications").json(), [])
        with patch.object(store, "environment", "production"):
            self.assertEqual(self.client.get("/guardians/guardian-demo/notifications").status_code, 501)

    def test_existing_handover_notification_survives_database_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "legacy-notifications.db")
            database = SQLiteStore(path, environment="test")
            contract = self._make_handover_contract(database=database)
            database.check_handover_timeout(contract.id, "legacy-handover")
            original = database.list_guardian_notifications("guardian-demo")
            self.assertEqual(len(original), 1)

            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute(
                    """CREATE TABLE missed_handover_notifications (
                        id TEXT PRIMARY KEY,
                        guardian_id TEXT NOT NULL REFERENCES family_members(id),
                        contract_id TEXT NOT NULL UNIQUE REFERENCES supervision_contracts(id),
                        message TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )"""
                )
                connection.execute(
                    """INSERT INTO missed_handover_notifications
                       SELECT id, guardian_id, contract_id, message, created_at
                       FROM guardian_notifications"""
                )
                connection.execute("DROP TABLE guardian_notifications")
            for _ in range(2):
                reopened = SQLiteStore(path, environment="test")
                self.assertEqual(reopened.list_guardian_notifications("guardian-demo"), original)

    def test_handover_timeout_is_rejected_before_start(self) -> None:
        contract = self._make_handover_contract(
            start_offset=timedelta(hours=1),
            end_offset=timedelta(hours=2),
        )

        response = self.client.post(
            f"/supervision/contracts/{contract.id}/check-handover-timeout"
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("not due", response.json()["detail"])
        self.assertEqual(store.get_contract(contract.id).status, "CONFIRMED")

    def test_handover_timeout_does_not_escalate_active_contract(self) -> None:
        contract = self._make_handover_contract()
        endpoint = f"/supervision/contracts/{contract.id}/confirm-handover"
        confirmed = self.client.post(endpoint, json={"actor_id": "caregiver-demo"})
        self.assertEqual(confirmed.status_code, 200, confirmed.text)

        response = self.client.post(
            f"/supervision/contracts/{contract.id}/check-handover-timeout"
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "ACTIVE")
        events = store.list_audit_events(contract.id)
        self.assertEqual([event.action for event in events], ["HANDOVER_CONFIRMED"])


if __name__ == "__main__":
    unittest.main()
