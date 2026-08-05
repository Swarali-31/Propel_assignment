# KSPDB Fault Localization

Control-room system for the fictional **Karnataka State Power Distribution Board** (subdivision SD07). It turns pole live/dark telemetry into **one localized fault ticket per outage** — span, DT, or feeder — with drive-to coordinates and PIN code, then **auto-verifies restoration from telemetry**.

## Quick start

```bash
git clone https://github.com/Swarali-31/Propel_assignment
cd kspdb-outage
docker compose up --build
```

Open **http://localhost:8080** (API on **http://localhost:8000**).

The stack seeds ~3,000 poles across ~48 DTs on first boot. Use **Inject span fault** in the right-hand simulator panel.

## Public URL
**Backend:** https://propel-assignment-hqu6.onrender.com
**Frontend:** https://propel-assignment-5.onrender.com


## Local without Docker

```bash
# API
cd backend
pip install -r requirements.txt
set SEED_ON_STARTUP=true
uvicorn app.main:app --host 127.0.0.1 --port 8000

# UI (other terminal)
cd frontend
npm install
npm run dev
# open http://localhost:5173 — Vite proxies /api to backend if configured,
# or set vite proxy target to http://127.0.0.1:8000
```

Smoke check: `python backend/scripts/smoke_e2e.py`

## What works

| Capability | Status |
|------------|--------|
| Telemetry ingest (dedupe, seq order, batch) | Yes |
| Span / DT / feeder localization + grouping | Yes |
| Missing topology (≈60% DTs) via GPS inference | Yes |
| Dead-sensor vs outage; scheduled outage suppress | Yes |
| Ticket lifecycle; resolve blocked if still dark | Yes |
| Auto-verify on restoration telemetry | Yes |
| Operator console (list + OSM map + detail) | Yes |
| Fault simulator in UI | Yes |
| AI crew briefing (LLM or template fallback) | Yes |

## Docs map

| File | Contents |
|------|----------|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Data flow, localization algorithm, API, UI, AI feature |
| [`DEPLOYMENT.md`](DEPLOYMENT.md) | Prerequisites, env vars, troubleshooting |
| [`DECISIONS.md`](DECISIONS.md) | Decision log and assumptions |
| [`AI-WORKFLOW.md`](AI-WORKFLOW.md) | How AI was used while building |

## Simulator (reviewer path)

1. Open the console → right panel → **Inject span fault**
2. One ticket appears with asset label `Span P-… → P-…`, PIN, confidence
3. Acknowledge → Assign crew
4. **Mark resolved** while dark → system **rejects**
5. **Repair last fault** → poles go live → ticket **auto-verified**
6. Optional: **Kill device** and **Scheduled outage demo** → no fault ticket

CLI equivalent:

```bash
curl -X POST http://localhost:8000/api/simulator/inject -H 'Content-Type: application/json' -d '{"fault_type":"span"}'
curl -X POST http://localhost:8000/api/simulator/repair -H 'Content-Type: application/json' -d '{}'
```

## Tests

```bash
cd backend && pip install -r requirements.txt && pytest -q
```

## Stack

FastAPI + SQLAlchemy/SQLite · React + Vite + Leaflet (OSM) · Docker Compose
