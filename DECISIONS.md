# Decisions

Newest first.

## 2026-08-1 — SQLite instead of Postgres

**Chose:** SQLite + WAL in a Docker volume.  
**Rejected:** Postgres service in Compose.  
**Why:** One fewer moving part for `docker compose up` on reviewer laptops; scale of one subdivision demo fits easily.  
**Fragile:** writer contention under huge concurrent ingest — batch API mitigates; real ESCOM would use Postgres + queue.

## 2026-08-01 — Infer topology with Prim/MST from GPS

**Chose:** At seed (and conceptually at ETL), build `effective_parent_id` via distance-based Prim from the DT.  
**Rejected:** Span localization only on the 40% recorded DTs; “survey-only” answer; online learning from outage co-occurrence (interesting, needs months of data we don’t have).  
**Why:** Brief requires something that works *today* for the 60%. Inference is honest about error modes (parallel laterals).  
**Assumption:** Poles on a DT form a planar tree with nearest-neighbor structure approximating the wire. Wrong when two feeders’ geometry would be closer — but poles are already partitioned by `dt_id`, which helps.

## 2026-08-02 — No debounce delay in the simulator path

**Chose:** Localization runs immediately after state change / inject.  
**Rejected:** Always wait 25s debounce before ticketing.  
**Why:** Reviewers must see tickets in seconds; debounce hurts the demo video. Production would debounce flappy single-pole events — config key reserved.  
**Assumption:** Corroboration (≥2 dark poles / DT-wide) substitutes for time debounce in this build.

## 2026-08-03 — Silence ≠ dark

**Chose:** Offline devices keep last energized state; only explicit `energized=false` (or forced sim dark) counts.  
**Rejected:** Treat heartbeat timeout as power_lost.  
**Why:** Brief: silence is ambiguous; crying wolf on dead modems kills trust.  
**Gap:** fw 1.2 devices that go quiet on real outages need neighbor corroboration over time — simulator forces physical dark for those poles so demos still work; production should add “N neighbors dark + silence” rules.

## 2026-08-03 — AI = crew briefing, not localization

**Chose:** Optional LLM summary for operators.  
**Rejected:** LLM for span finding.  
**Why:** Frontier detection is deterministic, cheap, explainable; language is where models help.

## 2026-08-04 — Polling UI every 4s

**Chose:** HTTP polling.  
**Rejected:** WebSockets.  
**Why:** Fewer deploy failure modes behind free-tier proxies.

## 2026-08-04 — Synthetic scale ~3k poles, not 38.4k

**Chose:** Proportional subset.  
**Why:** FAQ allows it; keeps seed/boot fast for reviewers.

## 2026-08-04 — Auth stub omitted entirely

**Chose:** No login.  
**Why:** Explicitly out of scope.

## Assumptions (brief ambiguous → treated as true)

1. One subdivision only; feeder/DT IDs unique within it.
2. `pole_id` is authoritative for location when device swaps.
3. PIN missing (~3%): show coordinates; use any pincode from the affected component when the boundary pole lacks one.
4. Two faults ten minutes apart on the same span while still open = same ticket; after close, a new ticket may open.
5. Feeder fault = all DTs on that feeder simultaneously DT-dark.

## With two more weeks

- Heartbeat-timeout + neighbor-vote path for fw 1.2 silence
- Postgres + Redis stream for multi-subdivision ingest
- Active learning: correct inferred edges when crews report actual spans
- Proper debounce and storm-mode rate limits on the UI

## Known wrong / fragile

- Inferred topology error on tight parallel spurs
- Seeded network geometry is idealized (random bearings), not real Bangalore streets
- Single SQLite writer under adversarial load
- No authentic NB-IoT/MQTT adapter (documented only)
