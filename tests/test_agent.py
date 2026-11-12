import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ollama import ResponseError

# Isolate the module-level store before importing the agent/tool registry.
_test_database_directory = tempfile.TemporaryDirectory()
os.environ["CARESYNC_DATABASE_PATH"] = os.path.join(_test_database_directory.name, "test.db")
os.environ["CARESYNC_ENV"] = "test"

from backend.agent.agent import AgentOutputError, AgentUnavailableError, CareSyncAgent


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
        self.assertFalse(client.requests[-1]["stream"])
        self.assertIn("properties", client.requests[-1]["format"])

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
