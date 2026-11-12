# CareSync AI

CareSync AI is a family supervision coordination project. Its goal is to help parents and caregivers keep agreed supervision arrangements up to date when schedules or availability change.

## Current implementation

- FastAPI `GET /` and `GET /health` endpoints.
- Typed models for families, family members, caregiver availability, supervision contracts and proposals, approvals, child events, and audit events.
- `GET /families/{family_id}` and `GET /families/{family_id}/members`.
- `POST /supervision/contracts`, `GET /supervision/contracts/{contract_id}`, `POST /supervision/contracts/{contract_id}/cancel`, and `GET /supervision/contracts/{contract_id}/proposals`.
- Timezone-aware timestamps and basic contract/member validation.

The API persists families, members, and contracts in SQLite at `backend/data/caresync.db` by default. Set `CARESYNC_DATABASE_PATH` to use another database file. A fictional demo family is inserted when `CARESYNC_ENV` is `development` (the default) or `test`; set it to `production` to disable demo seeding. Proposal lifecycle and persistence behavior, approval workflows, agent interactions, Ollama, WSO2 Agent Manager, and ThunderID are planned work and are not implemented yet.

## Technology stack

| Component | Technology | Status |
|---|---|---|
| AI model | Ollama / Qwen3 | Planned |
| Agent framework | Python; LangGraph is under consideration for workflow orchestration | Python backend implemented; agent and LangGraph planned |
| Backend API | FastAPI | Implemented |
| Frontend | React / Tailwind CSS | Planned |
| Database | SQLite | Implemented for families, members, and contracts; proposal persistence endpoints pending |
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
