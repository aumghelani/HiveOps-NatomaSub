# HiveOps

A human-in-the-loop incident response dashboard: a multi-agent pipeline (triage, root cause, remediation, sandbox verification) assembles an evidence dossier, and a human approves or rejects the fix.

**Live demo:** https://www.hiveops.us

Hackathon project built at YHacks 2026.

![HiveOps incident dashboard](https://github.com/user-attachments/assets/adb215fe-6511-4d30-af96-a77c589b8dbc)
<img width="1115" height="723" alt="HiveOps incident detail view" src="https://github.com/user-attachments/assets/def8d5a2-db37-44dd-a3b8-0fd519a81eca" />
![HiveOps screenshot](https://github.com/user-attachments/assets/a1213ce5-34b0-4bbf-b902-19b11f928965)

## Overview

HiveOps models how an operations team could use LLM agents to handle production incidents without giving up control. An incident arrives through a webhook, moves through a fixed sequence of agent stages, and ends in an `awaiting_approval` state with an evidence package: root cause, proposed fix, risk level, similar past incidents, and a before/after sandbox diff. Nothing is marked resolved until a reviewer approves it.

The project has a FastAPI backend and a React + TypeScript frontend. The backend runs entirely in memory with no credentials, so the full flow can be demoed locally.

## Key Features

- **Staged incident pipeline.** Triage, root cause analysis (log scan, blast radius, hypothesis), remediation (playbook selection), sandbox verification, and a reviewer summary. Each stage creates sub-tickets with confidence scores and writes audit log entries.
- **LLM agent modules.** Each stage has its own agent class with a system prompt and strict-JSON output parsing. The root cause and verification agents collect context through mocked tools (log search, recent deployments, config diff, sandbox command). Agents call a `fast`, `default` or `strong` model tier. If JSON parsing fails, the call is retried on the strong tier. The root cause agent also escalates when its confidence is below 0.60.
- **Human approval gate.** `POST /api/approvals/{id}/decide` accepts `approved`, `rejected` or `revision_requested`. Only approval moves an incident to `resolved`.
- **Evidence dossier.** Aggregates root cause, proposed fix, confidence, risk level, matched playbook, similar incidents and the sandbox before/after diff into one package.
- **Progressive-disclosure memory.** Past-incident lookup in four stages (0: count, 1: headlines, 2: causal signature and resolution summary, 3: full resolution path). It matches on service and causal signature through the Hex API and falls back to built-in sample data when Hex is not configured.
- **Mocked sandbox.** Simulated infrastructure actions (pool resize, config rollback, pod restart, and others) that return before/after state and a diff. Nothing touches real infrastructure.
- **QueenBee assistant.** A chat endpoint that adds the incident currently open in the UI to the LLM prompt. It supports slash commands (`/summary`, `/rootcause`, `/approve`, `/risk`, `/similar`, `/fix`, `/blast`, and others) and falls back to rule-based answers built from in-memory data when the LLM API is unavailable.
- **Live updates.** A WebSocket pushes incident state every second, a Server-Sent Events endpoint streams agent reasoning text, and React Query polls as a fallback.
- **Dashboard UI.** Five pages (Incidents, Incident Detail, Memory Bank, Playbooks, Audit Log), a split view for incidents, light/dark themes, a mobile tab bar, and a demo panel with seven preset incident scenarios.

## Architecture

```
 React + TypeScript (Vite)                FastAPI backend
 +-----------------------+   REST /api   +------------------------------------+
 | Incidents / Detail    | ------------> | routes: incidents, agents,         |
 | Memory / Playbooks    |   WebSocket   |         approvals, chat            |
 | Audit Log / QueenBee  | <------------ |                                    |
 | Demo trigger panel    |   SSE stream  | pipeline:                          |
 +-----------------------+ <------------ |  triage -> root cause ->           |
                                         |  remediation -> verification ->    |
                                         |  reviewer summary                  |
                                         |        |            |              |
                                         |  memory loader   sandbox runner    |
                                         |  (Hex API or     (mocked infra)    |
                                         |   sample data)                     |
                                         +--------+---------------------------+
                                                  |
                                   LLM API via Lava gateway (optional)
```

Incident lifecycle: `incoming -> triage -> investigating -> awaiting_approval -> resolved | rejected`

## Tech Stack

Python, FastAPI, Uvicorn, Pydantic, pydantic-settings, httpx, tenacity, WebSockets, Server-Sent Events, TypeScript, React, Vite, Tailwind CSS, Framer Motion, Zustand, TanStack React Query, React Router, Axios, LLM API (Lava gateway), Hex API, PostgreSQL schema (Supabase), Vercel

## Getting Started

Prerequisites: Python 3.11 (pinned in `ops-agent/backend/.python-version`) and Node.js 20.19+ or 22.12+ (required by Vite 8).

**Backend** (http://localhost:8000):

```bash
git clone https://github.com/aumghelani/HiveOps-NatomaSub.git
cd HiveOps-NatomaSub/ops-agent/backend
python3.11 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

**Frontend** (http://localhost:5173), in a second terminal:

```bash
cd HiveOps-NatomaSub/ops-agent/frontend
npm install
npm run dev
```

In development, Vite proxies `/api` and `/ws` to `localhost:8000`. Open http://localhost:5173 and use the demo trigger panel to create an incident and follow it through the pipeline.

On Windows, `npm run install:all` followed by `npm run dev` from `ops-agent/` does both steps and runs the two servers together. These root scripts use Windows virtualenv paths (`.venv\Scripts`), so on macOS/Linux use the commands above.

Production frontend build: `cd ops-agent/frontend && npm run build`.

### Configuration

No configuration is needed for the local demo. Optional backend variables go in `ops-agent/backend/.env` (template: `.env.example`):

| Variable | Purpose |
|----------|---------|
| `LAVA_API_KEY`, `LAVA_BASE_URL` | LLM gateway used by the agent modules and QueenBee |
| `LAVA_FAST_MODEL`, `LAVA_DEFAULT_MODEL`, `LAVA_STRONG_MODEL` | Model tiers |
| `HEX_API_TOKEN`, `HEX_PROJECT_ID` | Hex memory queries (sample data is used if unset) |
| `SUPABASE_URL`, `SUPABASE_KEY` | Supabase client initialization |

For the frontend, set `VITE_API_URL` to the deployed backend's base URL in production builds.

## API Overview

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/incidents/webhook` | Create an incident and start the pipeline |
| GET | `/api/incidents/` | List incidents |
| GET/PATCH | `/api/incidents/{id}` | Get or update an incident |
| GET | `/api/incidents/{id}/sub-tickets` | Agent sub-tasks |
| GET | `/api/incidents/{id}/evidence` | Evidence dossier |
| GET | `/api/incidents/{id}/memory/{stage}` | Memory matches, stage 0-3 |
| GET | `/api/incidents/{id}/logs` | Agent audit log |
| GET | `/api/incidents/{id}/stream/{agent}` | SSE agent reasoning stream |
| GET | `/api/agents/...` | Agent task and log lookups |
| GET | `/api/approvals/pending`, `/api/approvals/history` | Approval queue and history |
| POST | `/api/approvals/{id}/decide` | Approve / reject / request revision |
| POST | `/api/chat/` | QueenBee assistant |
| WS | `/ws/incidents/{id}` | Live incident updates |
| GET | `/health` | Health check |

## Project Structure

```
ops-agent/
├── package.json              Root scripts (runs backend + frontend together)
├── backend/
│   ├── requirements.txt
│   ├── .env.example
│   └── app/
│       ├── main.py           FastAPI app, CORS, WebSocket, router registration
│       ├── config.py         Settings loaded from environment
│       ├── database.py       Optional Supabase client
│       ├── migrations.sql    PostgreSQL schema (9 tables)
│       ├── api/
│       │   ├── pipeline.py   Pipeline runner, in-memory stores, evidence assembly
│       │   └── routes/       incidents, agents, approvals, chat
│       ├── agents/           orchestrator, triage, root cause, remediation,
│       │                     verification, reviewer summary
│       ├── memory/           Hex client, progressive-disclosure loader, write-back
│       ├── sandbox/          Mocked infrastructure runner
│       ├── schemas/          Pydantic models
│       └── utils/            LLM gateway client, tool executor, JSON logger
└── frontend/
    ├── vercel.json
    ├── vite.config.ts
    └── src/
        ├── api/              Axios client
        ├── components/       incident views, QueenBee chat, layout, hive UI kit
        ├── hooks/            React Query and WebSocket hooks
        ├── pages/            Incidents, IncidentDetail, MemoryBank, Playbooks, AuditLog
        ├── store/            Zustand store
        ├── mock/             Sample data for UI fallback
        └── types/
```

## Limitations and Roadmap

This is a hackathon prototype. Current limitations:

- **The live pipeline is scripted.** The webhook currently runs `run_pipeline_mock`, which produces fixed stage results (a database connection-pool exhaustion scenario) no matter which demo scenario is triggered. The LLM agent classes are implemented but not yet connected to the pipeline. QueenBee chat is the only feature that calls the LLM API at runtime.
- **In-memory state.** Incidents, logs and approvals are stored in Python dictionaries and are lost on restart. The Supabase client and `migrations.sql` schema exist, but no reads or writes go through them yet. Memory write-back after approval only logs the event.
- **Sample data in the UI.** The Memory Bank, Playbooks and Audit Log pages use bundled sample data. The Incident Detail page falls back to sample data when the backend is unreachable.
- **No automated tests.** `backend/tests/` is empty.
- **Permissive CORS.** CORS allows all origins, which is fine for a demo and should be restricted before real use.

Roadmap:

- Connect the orchestrator and agent classes to the webhook pipeline.
- Persist incidents, approvals and resolved-incident memory to PostgreSQL.
- Serve playbooks and memory patterns from the API instead of sample data.
- Add backend unit and API tests, and frontend component tests.
- Make the root dev scripts work across platforms.

## License

MIT. See [LICENSE](LICENSE).
