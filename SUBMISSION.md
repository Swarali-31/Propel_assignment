# Submission checklist & email draft

## Acceptance gates

| Gate | Status | Notes |
|------|--------|-------|
| G1 Public GitHub repo | Ready* | Create repo, push `kspdb-outage` |
| G2 `docker compose up` | Ready | See `DEPLOYMENT.md` |
| G3 Seeded on startup | Yes | ~3,015 poles, ~44 DTs |
| G4 Public URL | Ready | Railway / Render / Fly free tier |
| G5 Simulator on URL | Yes | Right panel in UI |
| G6 5-min demo video | **You do this** | Loom / YouTube unlisted |

## Local verify (already run)

```text
python backend/scripts/smoke_e2e.py
# inject 1 span ticket + PIN, resolve blocked, kill/scheduled=0 tickets,
# repair -> verified, 3 DT injects -> 3 tickets
pytest  → 8 passed
```

## Email body (<300 words) — paste and fill URLs

Subject: KSPDB Fault Localization — AI Product Engineer take-home

Hi,

Repo: \<GITHUB_URL\>  
Live: \<PUBLIC_URL\> (free tier may cold-start ~30–60s)  
Demo video: \<VIDEO_URL\>

What works: pole telemetry ingest; live/dark frontier localization to span/DT/feeder with one ticket per fault; GPS topology inference for the ~60% of DTs without wiring data; dead-modem and scheduled-outage suppression; ticket lifecycle where resolve is blocked until telemetry restores and verification is automatic; operator console + in-UI simulator; optional LLM crew briefing with template fallback.

What I cut: production MQTT adapter, WebSockets (polling instead), Postgres, heartbeat-neighbor voting for fw 1.2 silence without forced state, crew routing/auth/analytics (out of scope).

What I'd fix first: harden fw 1.2 silence detection using neighbor corroboration without the simulator's force-dark shortcut.

Thanks,  
\<Name\>

## How to finish deploy (15–30 min)

1. Create a public GitHub repo and push:
   ```bash
   cd kspdb-outage
   git remote add origin https://github.com/<you>/kspdb-outage.git
   git push -u origin master
   ```
2. Deploy with Docker on Railway/Render/Fly (Dockerfile compose or two services). Expose frontend port 80; proxy `/api` to backend as in `frontend/nginx.conf`.
3. Record: Inject span → one ticket → ack/assign → resolve blocked → Repair → verified → Kill device / Scheduled demo (no ticket).
4. Update `README.md` Live demo + video links.
5. Send the email.
