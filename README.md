# CareSync AI

An intelligent AI agent for child safety and family supervision coordination, powered by Ollama and WSO2 Agent Manager.

## Overview

CareSync AI helps parents and caregivers coordinate child supervision when daily routines and schedules change unexpectedly.

Unlike traditional child monitoring systems that simply track movement or send alerts, CareSync AI understands family context, identifies supervision gaps, proposes alternative arrangements, and coordinates caregiver handovers through human-approved decisions.

## Key Features

- **Intelligent Supervision Planning:** Detects gaps in supervision and proposes alternative caregiver arrangements.
- **Supervision Contracts:** Maintains explicit, time-bound agreements for childcare responsibilities.
- **Human-in-the-Loop Approval:** Requires caregiver confirmation and parental approval before activating critical changes.
- **Context-Aware Safety:** Interprets movement events, missed check-ins, and unexpected changes.
- **Local AI Inference:** Uses Ollama to run language models locally.
- **Agent Observability and Evaluation:** Integrates WSO2 Agent Manager to monitor and evaluate agent behavior.
- **Privacy and Security:** Designed with minimal data collection and fine-grained authorization.

## Technology Stack

| Component | Technology |
|---|---|
| AI Model | Ollama / Qwen3 |
| Agent Framework | Python / LangGraph |
| Backend | FastAPI |
| Frontend | React / Tailwind CSS |
| Database | SQLite |
| Agent Management | WSO2 Agent Manager |
| Identity Management | ThunderID |

## Project Status

🚧 Under active development.

The initial proof of concept focuses on resolving caregiver scheduling conflicts using intelligent planning and human approval.

## License

MIT License.