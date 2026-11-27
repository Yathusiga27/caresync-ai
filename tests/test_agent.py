import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from ollama import ResponseError

# Isolate the module-level store before importing the agent/tool registry.
_test_database_directory = tempfile.TemporaryDirectory()
os.environ["CARESYNC_DATABASE_PATH"] = os.path.join(_test_database_directory.name, "test.db")
os.environ["CARESYNC_ENV"] = "test"

from backend.agent.agent import AgentOutputError, AgentUnavailableError, CareSyncAgent
from backend.models.supervision import (
    AvailabilitySource,
    AvailabilityStatus,
    CaregiverAvailability,
    CaregiverCancellationEvent,
    ContractStatus,
    DecisionStatus,
    SupervisionContract,
)
from backend.schemas import AgentChatContext, ChatHistoryMessage
from backend.store import store


def response(content="", tool_calls=None):
    return SimpleNamespace(
        message=SimpleNamespace(content=content, tool_calls=tool_calls or [])
    )


def tool_call(name, arguments):
    return SimpleNamespace(
        function=SimpleNamespace(name=name, arguments=arguments)
    )


class StubOllamaClient:
    def __init__(self, responses=None, error=None) -> None:
        self.responses = list(responses or [response('{"reply":"Hello","proposal":null}')])
        self.error = error
        self.requests = []

    def chat(self, **request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.responses.pop(0)


class CareSyncAgentTests(unittest.TestCase):
    def test_conflicted_contract_tools_produce_validated_alternative(self) -> None:
        contract_id = f"contract-agent-{uuid4().hex}"
        start_time = datetime.now(timezone(timedelta(hours=5, minutes=30))) + timedelta(days=1)
        end_time = start_time + timedelta(hours=4)
        contract = SupervisionContract(
            id=contract_id,
            family_id="family-demo",
            child_id="child-demo",
            caregiver_id="caregiver-demo",
            activity="After-school care",
            expected_location="Home",
            start_time=start_time,
            end_time=end_time,
            status=ContractStatus.CONFIRMED,
            approval_status=DecisionStatus.APPROVED,
            acceptance_status=DecisionStatus.ACCEPTED,
        )
        store.create_contract(contract)
        store.create_availability(
            CaregiverAvailability(
                id=f"availability-agent-{uuid4().hex}",
                caregiver_id="caregiver-alt-demo",
                start_time=start_time - timedelta(hours=1),
                end_time=end_time + timedelta(hours=1),
                availability_status=AvailabilityStatus.AVAILABLE,
                source=AvailabilitySource.CAREGIVER,
            )
        )
        store.process_caregiver_cancellation(
            CaregiverCancellationEvent(
                id=f"event-agent-{uuid4().hex}",
                contract_id=contract_id,
                caregiver_id="caregiver-demo",
                occurred_at=datetime.now(timezone.utc),
                reason="Unexpected appointment",
            )
        )

        client = StubOllamaClient(
            responses=[
                response(
                    tool_calls=[
                        tool_call("get_supervision_contract", {"contract_id": contract_id}),
                        tool_call("list_available_caregivers", {"contract_id": contract_id}),
                    ]
                ),
                response(),
                response(
                    json.dumps(
                        {
                            "reply": "The alternate caregiver is available, pending human approval.",
                            "proposal": {
                                "contract_id": contract_id,
                                "original_contract_version": 2,
                                "proposed_caregiver_id": "caregiver-alt-demo",
                                "reason": "Available for the full supervision interval.",
                            },
                        }
                    )
                ),
            ]
        )

        answer = CareSyncAgent(client=client).respond(
            f"Review contract {contract_id} and suggest an available replacement."
        )

        self.assertIsNotNone(answer.proposal)
        self.assertEqual(answer.proposal.contract_id, contract_id)
        self.assertEqual(answer.proposal.original_contract_version, 2)
        self.assertEqual(answer.proposal.proposed_caregiver_id, "caregiver-alt-demo")
        self.assertIsNone(answer.proposal_validation_error)
        tool_messages = [
            message
            for message in client.requests[1]["messages"]
            if isinstance(message, dict) and message.get("role") == "tool"
        ]
        contract_result = next(
            json.loads(message["content"])
            for message in tool_messages
            if message["tool_name"] == "get_supervision_contract"
        )
        self.assertEqual(contract_result["contract"]["status"], "CONFLICTED")
        self.assertEqual(contract_result["contract"]["version"], 2)
        caregiver_result = next(
            json.loads(message["content"])
            for message in tool_messages
            if message["tool_name"] == "list_available_caregivers"
        )
        self.assertIn(
            {"caregiver_id": "caregiver-alt-demo", "name": "Demo Backup Caregiver"},
            caregiver_result["caregivers"],
        )

    def test_requests_tools_and_parses_structured_response(self) -> None:
        client = StubOllamaClient(
            responses=[
                response(),
                response('{"reply":"I can help review the schedule.","proposal":null}'),
            ]
        )
        agent = CareSyncAgent(model="qwen3:4b", client=client)

        answer = agent.respond("Can you help me think through a pickup change?")

        self.assertEqual(answer.reply, "I can help review the schedule.")
        self.assertEqual(answer.model, "qwen3:4b")
        self.assertEqual(client.requests[0]["messages"][1]["role"], "user")
        self.assertEqual(len(client.requests[0]["tools"]), 3)
        self.assertTrue(all(request["think"] is False for request in client.requests))
        self.assertFalse(client.requests[-1]["stream"])
        self.assertIn("properties", client.requests[-1]["format"])

    def test_followup_includes_recent_conversation_before_current_question(self) -> None:
        client = StubOllamaClient(responses=[response(), response('{"reply":"Check the start time."}')])
        history = [
            ChatHistoryMessage(role="user", content="What is a handover?"),
            ChatHistoryMessage(role="assistant", content="A caregiver confirms supervision has started."),
        ]
        CareSyncAgent(client=client).respond("When can I do that?", history=history)
        self.assertEqual(client.requests[0]["messages"][1:], [
            *[entry.model_dump() for entry in history],
            {"role": "user", "content": "When can I do that?"},
        ])

    def test_chat_with_selected_schedule_supplies_fresh_records_in_one_call(self) -> None:
        client = StubOllamaClient(responses=[response('{"reply":"Library pickup."}')])
        with patch("backend.agent.agent.dispatch_tool", side_effect=[
            {"ok": True, "contract": {"activity": "Library pickup"}},
            {"ok": True, "caregivers": []},
        ]) as lookup:
            answer = CareSyncAgent(client=client).respond(
                "Summarize this schedule.", context=AgentChatContext(contract_id="contract-1"),
            )
        self.assertEqual(answer.reply, "Library pickup.")
        self.assertEqual(len(client.requests), 1)
        self.assertEqual([call.args for call in lookup.call_args_list], [
            ("get_supervision_contract", {"contract_id": "contract-1"}),
            ("list_available_caregivers", {"contract_id": "contract-1"}),
        ])
        self.assertIn("Library pickup", client.requests[0]["messages"][-1]["content"])

    def test_general_chat_cannot_offer_a_schedule_specific_proposal(self) -> None:
        client = StubOllamaClient(responses=[response(json.dumps({
            "reply": "Choose a schedule to see current availability.",
            "proposal": {"contract_id": "unrelated", "original_contract_version": 1,
                         "proposed_caregiver_id": "caregiver-x", "reason": "Available"},
        }))])
        with patch("backend.agent.agent.dispatch_tool") as lookup:
            answer = CareSyncAgent(client=client).respond("Who is available?", context=AgentChatContext())
        lookup.assert_not_called()
        self.assertIsNone(answer.proposal)
        self.assertTrue(answer.proposal_validation_error)

    def test_executes_read_only_tool_and_sends_result_back(self) -> None:
        client = StubOllamaClient(
            responses=[
                response(tool_calls=[tool_call("get_supervision_contract", {"contract_id": "missing"})]),
                response(),
                response('{"reply":"I could not find that contract.","proposal":null}'),
            ]
        )
        agent = CareSyncAgent(client=client)

        answer = agent.respond("Review contract missing")

        self.assertIsNone(answer.proposal)
        tool_messages = [m for m in client.requests[1]["messages"] if isinstance(m, dict) and m.get("role") == "tool"]
        self.assertEqual(json.loads(tool_messages[0]["content"]), {"ok": False, "error": "Supervision contract not found"})

    def test_invalid_candidate_is_suppressed(self) -> None:
        client = StubOllamaClient(
            responses=[
                response(),
                response('{"reply":"A replacement may be possible.","proposal":{"contract_id":"missing","original_contract_version":1,"proposed_caregiver_id":"caregiver-x","reason":"Suggested alternative"}}')
            ]
        )
        agent = CareSyncAgent(client=client)

        answer = agent.respond("Suggest a replacement")

        self.assertIsNone(answer.proposal)
        self.assertEqual(answer.proposal_validation_error, "Supervision contract was not found.")

    def test_connection_error_has_actionable_message(self) -> None:
        agent = CareSyncAgent(client=StubOllamaClient(error=ConnectionError("offline")))

        with self.assertRaisesRegex(AgentUnavailableError, "Start Ollama"):
            agent.respond("Hello")

    def test_missing_model_has_pull_instruction(self) -> None:
        client = StubOllamaClient(error=ResponseError("model not found", status_code=404))
        agent = CareSyncAgent(model="qwen3:4b", client=client)

        with self.assertRaisesRegex(AgentUnavailableError, "ollama pull qwen3:4b"):
            agent.respond("Hello")

    def test_tool_loop_is_bounded(self) -> None:
        calls = [response(tool_calls=[tool_call("get_supervision_contract", {"contract_id": "missing"})]) for _ in range(3)]
        client = StubOllamaClient(responses=calls)
        agent = CareSyncAgent(client=client)

        with self.assertRaisesRegex(AgentOutputError, "tool-call limit"):
            agent.respond("Keep calling tools")


if __name__ == "__main__":
    unittest.main()
