# Deployment

## Prerequisites

| Tool | Version tested |
|------|----------------|
| Docker | 24+ |
| Docker Compose | v2 (`docker compose`) |
| (optional) Python | 3.12+ for local pytest |
| (optional) Node | 22+ for frontend dev |

## One-command bring-up

```bash
cp .env.example .env   # optional
docker compose up --build
```

Wait until backend healthcheck passes (first boot seeds the network — often 20–60s).

### Verify

1. Open http://localhost:8080 — control room UI, non-zero pole count in the top bar.
2. `curl http://localhost:8000/health` → `{"ok":true,...}`
3. Click **Inject span fault** → exactly one new incident with a PIN.
4. Click **Repair last fault** → ticket moves to **verified**.

API docs: http://localhost:8000/docs

## Environment variables

| Name | Required | Default | Purpose |
|------|----------|---------|---------|
| `DATA_DIR` | no | `/data` in Docker | SQLite + runtime files |
| `DATABASE_URL` | no | `sqlite:////data/kspdb.db` | SQLAlchemy URL |
| `SEED_ON_STARTUP` | no | `true` | Generate network if DB empty |
| `CORS_ORIGINS` | no | `*` | Browser origins |
| `OPENAI_API_KEY` | no | empty | Enables LLM briefings |
| `OPENAI_MODEL` | no | `gpt-4o-mini` | Chat model |
| `DEBOUNCE_SECONDS` | no | `25` | Reserved for future debounce |

Commit `.env.example` only — never commit real keys.

## Reset to clean state

```bash
docker compose down -v
docker compose up --build
```

`-v` deletes the `kspdb_data` volume so seed runs again.

## Local backend (without Docker)

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
set SEED_ON_STARTUP=true   # PowerShell: $env:SEED_ON_STARTUP="true"
uvicorn app.main:app --reload --port 8000
```

```bash
cd frontend
npm install
npm run dev
```

Point Vite proxy at `http://127.0.0.1:8000` if not using Compose networking.

## Public deploy (outline)

Any host that can run Compose or two containers works. Typical free-tier path:

1. Build/push images or connect the GitHub repo to Railway/Render/Fly.
2. Expose the **frontend** (port 80) publicly; keep backend private on the same Docker network, proxied via nginx as in this repo.
3. Set `OPENAI_API_KEY` in the host secrets if you want live LLM briefings.
4. Paste the URL into `README.md`. Mention cold starts.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `docker compose up` then empty UI / API errors | Backend still seeding | Wait for healthcheck; `docker compose logs backend` |
| Port 8080 already allocated | Local conflict | Change `"8080:80"` in compose, or stop the other process |
| Backend unhealthy loop | Seed/crash on start | `docker compose logs backend`; ensure volume writable |
| Map tiles blank | OSM blocked / offline | Network allow `*.tile.openstreetmap.org`; map poles still plot |
| CORS errors in local Vite | Hitting `:8000` from `:5173` without proxy | Use Vite proxy or set `CORS_ORIGINS=http://localhost:5173` |
| Inject span → 0 tickets | Landed on scheduled DT / bad pick | Use **suggest-span** path (UI button does); clear demos with `down -v` |
| Resolve always 409 | Poles still dark | Run **Repair last fault** first — by design |
| `pytest` fails on Windows paths | Old DB URL | Delete `backend/.data-test`; re-run |
| ARM Mac image slow/fail | Emulation | Prefer native arm64 builds; official python/node images support arm64 |
| Free-tier sleep | Cold start | Document in README; wait 30–60s on first request |
| WebSocket upgrade (N/A here) | — | We poll; avoids proxy WS issues |
| Briefing says template | No API key | Expected; set `OPENAI_API_KEY` for LLM |
| SQLite locked errors under heavy burst | Writers contend | Use batch endpoint; WAL is enabled; for prod move to Postgres |

## Measured smoke (copy/paste)

```bash
curl -s -o /dev/null -w "%{http_code} %{time_total}\n" http://localhost:8000/api/tickets
curl -s -X POST http://localhost:8000/api/simulator/inject -H "Content-Type: application/json" -d "{\"fault_type\":\"span\"}"
```
