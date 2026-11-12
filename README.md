# CareSync AI

CareSync AI is a family supervision coordination project. Its goal is to help parents and caregivers keep agreed supervision arrangements up to date when schedules or availability change.

## Current implementation

- FastAPI `GET /` and `GET /health` endpoints.
- Typed models for families, family members, caregiver availability, supervision contracts and proposals, approvals, child events, and audit events.
- `GET /families/{family_id}` and `GET /families/{family_id}/members`.
- `POST /caregivers/{caregiver_id}/availability` and `GET /caregivers/{caregiver_id}/availability`.
- `POST /supervision/contracts`, `GET /supervision/contracts/{contract_id}`, `POST /supervision/contracts/{contract_id}/cancel`, and `GET /supervision/contracts/{contract_id}/proposals`.
- `POST /agent/events` processes caregiver cancellation events deterministically: it checks full-window availability, creates a versioned replacement proposal, or escalates the contract when no alternative is available. Repeated event IDs are idempotent.
- `GET /approvals/pending`, guardian approve/reject endpoints, and caregiver proposal accept/decline endpoints. A replacement contract is created only after both positive responses and a final version and availability check.
- `POST /supervision/contracts/{contract_id}/confirm-handover` records the assigned caregiver's handover confirmation, activates an in-window contract, and writes an audit event.
- `POST /supervision/contracts/{contract_id}/check-handover-timeout` escalates a confirmed contract with no handover once its scheduled start time is reached. Repeated checks are safe.
- Timezone-aware timestamps and basic contract/member validation.

The API persists families, members, caregiver availability, contracts, proposals, approval responses, cancellation events, and audit events in SQLite at `backend/data/caresync.db` by default. Set `CARESYNC_DATABASE_PATH` to use another database file. A fictional demo family is inserted when `CARESYNC_ENV` is `development` (the default) or `test`; set it to `production` to disable demo seeding. Approval, handover, and timeout actions are enabled only in development and test environments until authentication and a trusted scheduler are configured. Actor IDs are local demo checks, not authentication. The timeout check must currently be invoked explicitly; there is no background scheduler yet. The language-model agent, Ollama, WSO2 Agent Manager, and ThunderID are planned work and are not implemented yet.

## Technology stack

| Component | Technology | Status |
|---|---|---|
| AI model | Ollama / Qwen3 | Planned |
| Agent framework | Python; LangGraph is under consideration for workflow orchestration | Python backend implemented; agent and LangGraph planned |
| Backend API | FastAPI | Implemented |
| Frontend | React / Tailwind CSS | Planned |
| Database | SQLite | Implemented for family coordination data and cancellation events |
| Agent management | WSO2 Agent Manager | Planned |
| Identity management | ThunderID | Planned |

## Run locally

From the repository root, create and activate a virtual environment, then install the backend dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
```

Start the development server:

```bash
uvicorn backend.main:app --reload
```

The API is available at `http://127.0.0.1:8000`; interactive API documentation is at `http://127.0.0.1:8000/docs`.

## Run tests

```bash
python -m unittest discover -s tests
```

## License

MIT. See [LICENSE](LICENSE).
