"""Read-only tools exposed to the CareSync chat model."""

from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from backend.store import store


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContractIdArguments(ToolArguments):
    contract_id: str


class ReplacementCandidateArguments(ContractIdArguments):
    proposed_caregiver_id: str
    original_contract_version: int


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_supervision_contract",
            "description": "Get the current details and version of a supervision contract.",
            "parameters": {
                "type": "object",
                "required": ["contract_id"],
                "properties": {"contract_id": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_available_caregivers",
            "description": "List active alternative caregivers available for the full contract interval.",
            "parameters": {
                "type": "object",
                "required": ["contract_id"],
                "properties": {"contract_id": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate_replacement_candidate",
            "description": "Deterministically validate a suggested replacement for the contract's current version.",
            "parameters": {
                "type": "object",
                "required": ["contract_id", "proposed_caregiver_id", "original_contract_version"],
                "properties": {
                    "contract_id": {"type": "string"},
                    "proposed_caregiver_id": {"type": "string"},
                    "original_contract_version": {"type": "integer", "minimum": 1},
                },
            },
        },
    },
]


def dispatch_tool(name: str, raw_arguments: Any) -> dict[str, Any]:
    """Validate arguments and call one of the explicitly allowed read-only tools."""
    try:
        if name == "get_supervision_contract":
            args = ContractIdArguments.model_validate(raw_arguments)
            return {"ok": True, "contract": store.get_agent_contract_context(args.contract_id)}
        if name == "list_available_caregivers":
            args = ContractIdArguments.model_validate(raw_arguments)
            return {"ok": True, "caregivers": store.list_available_caregivers_for_contract(args.contract_id)}
        if name == "validate_replacement_candidate":
            args = ReplacementCandidateArguments.model_validate(raw_arguments)
            return {
                "ok": True,
                **store.validate_replacement_candidate(
                    args.contract_id,
                    args.proposed_caregiver_id,
                    args.original_contract_version,
                ),
            }
    except LookupError as error:
        return {"ok": False, "error": str(error)}
    except ValidationError:
        return {"ok": False, "error": "Invalid arguments for this tool."}
    return {"ok": False, "error": "Unknown tool."}
