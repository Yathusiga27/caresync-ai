import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
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
from backend.store import SQLiteStore, store


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
        contract = self._create_contract(
            f"2027-{day}T14:00:00+05:30", f"2027-{day}T18:00:00+05:30"
        )
        availability = self.client.post(
            "/caregivers/caregiver-alt-demo/availability",
            json={
                "start_time": f"2027-{day}T13:00:00+05:30",
                "end_time": f"2027-{day}T19:00:00+05:30",
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
    ) -> SupervisionContract:
        now = datetime.now(timezone.utc)
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
        store.create_contract(contract)
        return contract

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
        contract = self._create_contract(
            "2027-01-10T14:00:00+05:30", "2027-01-10T18:00:00+05:30"
        )
        availability = self.client.post(
            "/caregivers/caregiver-alt-demo/availability",
            json={
                "start_time": "2027-01-10T13:00:00+05:30",
                "end_time": "2027-01-10T19:00:00+05:30",
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

    def test_cancellation_escalates_when_alternative_is_unavailable(self) -> None:
        contract = self._create_contract(
            "2027-02-10T14:00:00+05:30", "2027-02-10T18:00:00+05:30"
        )
        available = self.client.post(
            "/caregivers/caregiver-alt-demo/availability",
            json={
                "start_time": "2027-02-10T13:00:00+05:30",
                "end_time": "2027-02-10T19:00:00+05:30",
                "availability_status": "AVAILABLE",
                "source": "CAREGIVER",
            },
        )
        self.assertEqual(available.status_code, 201, available.text)
        unavailable = self.client.post(
            "/caregivers/caregiver-alt-demo/availability",
            json={
                "start_time": "2027-02-10T15:00:00+05:30",
                "end_time": "2027-02-10T17:00:00+05:30",
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
        contract = self._create_contract(
            "2027-03-10T14:00:00+05:30", "2027-03-10T18:00:00+05:30"
        )

        outcome = self._submit_cancellation(contract["id"], "event-no-alt-001")

        self.assertEqual(outcome["contract_status"], "ESCALATED")
        self.assertIsNone(outcome["proposal"])

    def test_duplicate_cancellation_event_does_not_create_another_proposal(self) -> None:
        contract = self._create_contract(
            "2027-05-10T14:00:00+05:30", "2027-05-10T18:00:00+05:30"
        )
        availability = self.client.post(
            "/caregivers/caregiver-alt-demo/availability",
            json={
                "start_time": "2027-05-10T13:00:00+05:30",
                "end_time": "2027-05-10T19:00:00+05:30",
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

    def test_caregiver_decline_escalates_contract(self) -> None:
        contract, proposal = self._make_proposal("08-10", "event-decline-001")

        declined = self.client.post(
            f"/proposals/{proposal['id']}/decline",
            json={"actor_id": "caregiver-alt-demo"},
        )

        self.assertEqual(declined.status_code, 200, declined.text)
        self.assertEqual(declined.json()["proposal"]["status"], "DECLINED")
        self.assertEqual(
            self.client.get(f"/supervision/contracts/{contract['id']}").json()["status"],
            "ESCALATED",
        )

    def test_stale_proposal_is_invalidated_instead_of_approved(self) -> None:
        contract, proposal = self._make_proposal("09-10", "event-stale-001")
        guardian_approval = self._approval_for(proposal["id"], "GUARDIAN")
        original = store.get_contract(contract["id"])
        original.version += 1
        original.updated_at = original.updated_at.replace(year=2028)
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
                "start_time": "2027-10-10T15:00:00+05:30",
                "end_time": "2027-10-10T17:00:00+05:30",
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
