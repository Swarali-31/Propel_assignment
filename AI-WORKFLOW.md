# AI Workflow

## Tools used

| Tool | Used for |
|------|----------|
| Cursor (Composer) | Majority of scaffolding: FastAPI services, React console, Docker, docs drafts |
| Manual editing | Localization edge cases, ticket resolve/verify rules, simulator physics knobs, design decisions |

## What was delegated vs handwritten

**Delegated wholesale:** boilerplate (FastAPI app wiring, Dockerfile/nginx, React map shell, CSS layout, markdown first passes).

**Written / tightly supervised:** `services/localization.py`, `services/topology.py`, `services/tickets.py` resolve semantics, simulator dying-message / fw-1.2 behaviour, tests in `tests/test_localization.py`.

**Line drawn:** anything that decides “is this a fault?” or “may the operator close?” was not accepted from the model without reading every branch.

## Where the AI was wrong (caught)

1. **Treating heartbeat timeout as `power_lost`.** An early draft marked silent devices dark immediately — violates the brief’s ambiguity of silence. Caught by re-reading `01-problem-context.md` §4 and adding `device_offline` with last-known-live semantics; tests cover it.

2. **One-ticket-per-component bug risk with overlapping dark roots.** Model suggested listing every dark pole’s parent edge as a separate fault. Caught while writing grouping_key upsert logic; tests assert a single open ticket for P3–P4 dark.

3. **Resolve button setting `verified` without telemetry.** Classic CRUD instinct. Caught against deliverable self-check (“mark resolved while dark → push back”); implemented 409 + `false_resolve_attempts`.

## How much of the final code is AI-generated

Rough estimate: **~70%** lines initially proposed by the assistant, **~30%** rewritten or authored for correctness (localization, tickets, simulator, tests). All of it was reviewed before shipping.

## Best prompts / session moves

- “Implement localization as live/dark frontier on a tree; sensors are nodes; faults are edges; never alert per dark pole.”
- “Show me how this fails when `parent_pole_id` is missing for 60% of DTs — ship an inferred topology and label confidence.”
- “Simulator must drop ~30% of dying messages and skip `power_lost` for fw 1.2; repair emits boot + power_restored.”

## Product AI feature

Documented in `ARCHITECTURE.md`: crew briefing only. No LLM in the localization path.
