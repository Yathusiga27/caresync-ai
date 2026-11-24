---
name: verify-caresync-workflow
description: Verify CareSync AI's caregiver cancellation and replacement workflow locally with fictional data. Use for regression checks, release verification, or a cancellation-to-handover smoke test.
---

# Verify the CareSync workflow

1. Record the current branch, commit, and working tree state. Test the checkout the user requested. Read the repository's `README.md`, `backend/main.py`, and relevant cases in `tests/test_main.py` for the current API and fixtures.
2. Run `python -B -m unittest discover -s tests` from the repository root with the dependencies in `backend/requirements.txt` installed. Use the existing virtual environment when available. On Windows, its interpreter is typically `.venv/Scripts/python.exe`.
3. For an end-to-end smoke test, use a fresh temporary SQLite file. Set `CARESYNC_DATABASE_PATH` and `CARESYNC_ENV=test` before importing the backend. Use fictional demo members only. Seed an already-confirmed original contract as a fixture because the contract creation endpoint starts in `PENDING_APPROVAL`. Submit the cancellation and subsequent responses through the public API.
4. Check the applicable invariants:
   - The assigned caregiver's cancellation creates a versioned proposal for an active alternative available for the full window. Replaying the event keeps the same proposal.
   - One human response leaves the replacement unconfirmed. Guardian approval and replacement caregiver acceptance together confirm it after the version and availability checks.
   - The assigned replacement caregiver can confirm handover within the supervision window. Early confirmation and confirmation by another caregiver are rejected.
   - With no viable replacement, or when a failed proposal escalates the contract, the guardian inbox and audit trail reflect the outcome. Exercise retries and confirm they produce a single notice.
5. Check live chat when the task calls for it. Use the configured Ollama host and model from the README or environment. The defaults are `http://127.0.0.1:11434` and `qwen3:4b`. Confirm the model is available before calling `POST /agent/chat` with a fictional contract. Verify that the suggestion matches the current contract version and caregiver availability, and that chat leaves persisted workflow state unchanged. An Ollama SDK install or a passing stubbed test provides evidence for those components only.

Report the tested commit, commands and results, state transitions observed in the smoke test, and any skipped checks with their blockers. During verification, keep source changes outside the task unless the user also asks for fixes.
