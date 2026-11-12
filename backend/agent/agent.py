"""Ollama-backed chat orchestration with bounded, read-only tools."""

import json
import os
from typing import Any, Optional

from ollama import Client, ResponseError
from pydantic import ValidationError

from backend.agent.tools import TOOLS, dispatch_tool
from backend.schemas import AgentChatResponse, AgentModelAnswer


DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen3:4b"
MAX_TOOL_ROUNDS = 3

SYSTEM_PROMPT = """You are CareSync AI, an assistant for family supervision coordination.
Use the available read-only tools to retrieve contract details and caregiver
availability when the user provides a contract ID. Never claim a caregiver is
available unless the tool confirms it. You cannot approve a plan, assign a
caregiver, or change a supervision contract. Any replacement suggestion is only
a recommendation for people to review and approve. Do not invent family details.
For your final response, return JSON matching the requested schema. Include a
proposal only when you have a contract ID, its current version, a candidate
caregiver ID, and a reason; otherwise leave proposal null."""


class AgentUnavailableError(RuntimeError):
    """Raised when Ollama or its configured model cannot serve a chat request."""


class AgentOutputError(RuntimeError):
    """Raised when Ollama does not return a valid structured answer."""


class CareSyncAgent:
    def __init__(
        self,
        model: Optional[str] = None,
        host: Optional[str] = None,
        client: Optional[Client] = None,
    ) -> None:
        self.model = model or os.getenv("CARESYNC_OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
        self.host = host or os.getenv("CARESYNC_OLLAMA_HOST", DEFAULT_OLLAMA_HOST)
        self._client = client or Client(host=self.host)

    def respond(self, message: str) -> AgentChatResponse:
        messages: list[Any] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": message},
        ]
        try:
            for _ in range(MAX_TOOL_ROUNDS):
                response = self._client.chat(
                    model=self.model,
                    messages=messages,
                    tools=TOOLS,
                    stream=False,
                )
                calls = getattr(response.message, "tool_calls", None) or []
                if not calls:
                    break
                messages.append(response.message)
                for call in calls:
                    function = call.function
                    result = dispatch_tool(function.name, function.arguments)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_name": function.name,
                            "content": json.dumps(result),
                        }
                    )
            else:
                raise AgentOutputError("Ollama exceeded the allowed tool-call limit.")

            structured = self._client.chat(
                model=self.model,
                messages=messages
                + [
                    {
                        "role": "system",
                        "content": "Now provide the final answer as JSON matching the schema.",
                    }
                ],
                format=AgentModelAnswer.model_json_schema(),
                stream=False,
            )
        except ConnectionError as error:
            raise AgentUnavailableError(
                "Cannot connect to Ollama. Start Ollama and check CARESYNC_OLLAMA_HOST."
            ) from error
        except ResponseError as error:
            if error.status_code == 404:
                raise AgentUnavailableError(
                    f"Ollama model '{self.model}' is not available. Pull it with "
                    f"'ollama pull {self.model}' and retry."
                ) from error
            raise AgentUnavailableError(
                f"Ollama could not complete the request: {error.error}"
            ) from error

        try:
            answer = AgentModelAnswer.model_validate_json(structured.message.content)
        except (ValidationError, ValueError, TypeError) as error:
            raise AgentOutputError("Ollama returned an invalid structured answer.") from error
        validation_error = None
        proposal = answer.proposal
        if proposal is not None:
            checked = dispatch_tool(
                "validate_replacement_candidate",
                {
                    "contract_id": proposal.contract_id,
                    "proposed_caregiver_id": proposal.proposed_caregiver_id,
                    "original_contract_version": proposal.original_contract_version,
                },
            )
            if not checked.get("valid", False):
                validation_error = checked.get("reason", checked.get("error", "Proposal could not be validated."))
                proposal = None
        return AgentChatResponse(
            reply=answer.reply,
            model=self.model,
            proposal=proposal,
            proposal_validation_error=validation_error,
        )
