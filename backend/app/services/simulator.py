"""Fault simulator — produces realistic telemetry for injected faults and noise."""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.models.db import DistributionTransformer, Pole, ScheduledOutage, SimulatorState
from app.services.ingest import TelemetryPayload, ingest_batch
from app.services.localization import apply_localization
from app.services.topology import children_map, descendants


class InjectFaultRequest(BaseModel):
    fault_type: str = Field(description="span | dt | feeder")
    # For span: downstream pole id (fault on edge into this pole)
    downstream_pole_id: str | None = None
    dt_id: str | None = None
    feeder_id: str | None = None
    # Physics knobs
    dying_message_success_rate: float = 0.70
    include_duplicates: bool = True
    shuffle_order: bool = True


class KillDeviceRequest(BaseModel):
    pole_id: str


class RepairFaultRequest(BaseModel):
    fault_id: str | None = None
    downstream_pole_id: str | None = None
    dt_id: str | None = None
    feeder_id: str | None = None


def _sim_state(db: Session) -> SimulatorState:
    row = db.get(SimulatorState, 1)
    if not row:
        row = SimulatorState(id=1)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _load_json(s: str) -> list:
    try:
        return json.loads(s or "[]")
    except json.JSONDecodeError:
        return []


def _kids_for_dt(db: Session, dt_id: str) -> tuple[dict[str, Pole], dict]:
    poles = db.query(Pole).filter(Pole.dt_id == dt_id).all()
    by_id = {p.id: p for p in poles}
    parents = {p.id: p.effective_parent_id for p in poles}
    return by_id, children_map(parents)


def _affected_from_span(db: Session, downstream_pole_id: str) -> list[Pole]:
    pole = db.get(Pole, downstream_pole_id)
    if not pole:
        raise ValueError("unknown pole")
    by_id, kids = _kids_for_dt(db, pole.dt_id)
    ids = [downstream_pole_id, *descendants(downstream_pole_id, kids)]
    return [by_id[i] for i in ids if i in by_id]


def _affected_from_dt(db: Session, dt_id: str) -> list[Pole]:
    return db.query(Pole).filter(Pole.dt_id == dt_id).all()


def _affected_from_feeder(db: Session, feeder_id: str) -> list[Pole]:
    return db.query(Pole).filter(Pole.feeder_id == feeder_id).all()


def _make_power_lost_messages(
    poles: list[Pole],
    *,
    success_rate: float,
    include_duplicates: bool,
    shuffle_order: bool,
    base_ts: datetime | None = None,
) -> list[TelemetryPayload]:
    base_ts = base_ts or datetime.utcnow()
    msgs: list[TelemetryPayload] = []
    for i, p in enumerate(poles):
        if not p.has_device:
            continue
        # Clock skew ±90s
        skew = timedelta(seconds=random.randint(-90, 90))
        ts = base_ts + skew + timedelta(milliseconds=i)
        seq = p.last_seq + 1 + random.randint(0, 2)

        fw = p.firmware
        # fw 1.2.x does not send power_lost — just goes quiet (no message)
        if fw.startswith("1.2"):
            continue

        if random.random() > success_rate:
            # Dying message failed — device goes silent; we mark de-energized via
            # a later heartbeat absence. For sim, inject an internal state change
            # by sending nothing; caller may force state.
            continue

        msgs.append(
            TelemetryPayload(
                device_id=p.device_id or f"DEV-{p.id}",
                pole_id=p.id,
                event="power_lost",
                energized=False,
                ts=ts,
                seq=seq,
                battery_mv=random.randint(3100, 3600),
                rssi=random.randint(-105, -70),
                fw=fw,
            )
        )
        if include_duplicates and random.random() < 0.15:
            msgs.append(msgs[-1].model_copy())

    if shuffle_order:
        random.shuffle(msgs)
    return msgs


def _force_dark(db: Session, poles: list[Pole]) -> None:
    """Ensure poles that failed to send dying messages are still dark in state."""
    now = datetime.utcnow()
    for p in poles:
        p.energized = False
        # fw1.2 and failed dying msgs: no last_seen update — looks like silence
        if p.has_device and not (p.firmware or "").startswith("1.2"):
            # If they sent a message, ingest will update; if not, still dark
            pass
        p.last_event_ts = now
    db.commit()


def inject_fault(db: Session, req: InjectFaultRequest) -> dict[str, Any]:
    if req.fault_type == "span":
        if not req.downstream_pole_id:
            suggestion = pick_demo_span(db)
            req.downstream_pole_id = suggestion.get("downstream_pole_id")
            if not req.downstream_pole_id:
                candidates = (
                    db.query(Pole)
                    .filter(Pole.effective_parent_id.isnot(None))
                    .filter(Pole.has_device.is_(True))
                    .limit(500)
                    .all()
                )
                if not candidates:
                    raise ValueError("no candidate poles")
                req.downstream_pole_id = random.choice(candidates).id
        affected = _affected_from_span(db, req.downstream_pole_id)
        meta = {
            "fault_type": "span",
            "downstream_pole_id": req.downstream_pole_id,
            "dt_id": affected[0].dt_id if affected else None,
            "feeder_id": affected[0].feeder_id if affected else None,
        }
    elif req.fault_type == "dt":
        dt_id = req.dt_id
        if not dt_id:
            dts = db.query(DistributionTransformer).all()
            dt_id = random.choice(dts).id
        affected = _affected_from_dt(db, dt_id)
        meta = {"fault_type": "dt", "dt_id": dt_id, "feeder_id": affected[0].feeder_id if affected else None}
    elif req.fault_type == "feeder":
        feeder_id = req.feeder_id
        if not feeder_id:
            feeder_id = random.choice(db.query(Pole.feeder_id).distinct().all())[0]
        affected = _affected_from_feeder(db, feeder_id)
        meta = {"fault_type": "feeder", "feeder_id": feeder_id}
    else:
        raise ValueError("fault_type must be span|dt|feeder")

    msgs = _make_power_lost_messages(
        affected,
        success_rate=req.dying_message_success_rate,
        include_duplicates=req.include_duplicates,
        shuffle_order=req.shuffle_order,
    )
    # Apply telemetry WITHOUT localizing yet — partial dying messages would
    # otherwise open bogus mid-line span tickets before force_dark runs.
    ingest_result = (
        ingest_batch(db, msgs, run_localization=False)
        if msgs
        else {"accepted": 0, "rejected": 0, "tickets_created": [], "affected_dts": []}
    )
    _force_dark(db, affected)
    tickets = apply_localization(
        db,
        dt_ids=list({p.dt_id for p in affected}),
    )
    from app.models.db import Ticket, TicketStatus

    open_statuses = [
        TicketStatus.DETECTED.value,
        TicketStatus.ACKNOWLEDGED.value,
        TicketStatus.CREW_ASSIGNED.value,
        TicketStatus.RESOLVED.value,
    ]
    # Prefer tickets created this call; else open tickets whose affected set overlaps
    ticket_ids = [t.id for t in tickets]
    if not ticket_ids:
        affected_set = set(p.id for p in affected)
        for t in db.query(Ticket).filter(Ticket.status.in_(open_statuses)).all():
            try:
                ids = set(json.loads(t.affected_pole_ids or "[]"))
            except json.JSONDecodeError:
                ids = set()
            if ids & affected_set or (
                req.fault_type == "feeder" and t.feeder_id == meta.get("feeder_id") and t.fault_type == "feeder"
            ):
                ticket_ids.append(t.id)

    fault_id = f"SIM-{datetime.utcnow().strftime('%H%M%S')}-{random.randint(100, 999)}"
    state = _sim_state(db)
    active = _load_json(state.active_faults_json)
    record = {
        "id": fault_id,
        **meta,
        "affected_pole_ids": [p.id for p in affected],
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    active.append(record)
    state.active_faults_json = json.dumps(active)
    db.commit()

    return {
        "fault": record,
        "telemetry_sent": len(msgs),
        "ingest": ingest_result,
        "tickets_created": list(dict.fromkeys(ticket_ids)),
        "affected_count": len(affected),
    }


def repair_fault(db: Session, req: RepairFaultRequest) -> dict[str, Any]:
    state = _sim_state(db)
    active = _load_json(state.active_faults_json)
    target = None
    if req.fault_id:
        target = next((f for f in active if f["id"] == req.fault_id), None)
    elif req.downstream_pole_id:
        target = next((f for f in active if f.get("downstream_pole_id") == req.downstream_pole_id), None)
    elif req.dt_id:
        target = next((f for f in active if f.get("dt_id") == req.dt_id and f.get("fault_type") == "dt"), None)
    elif req.feeder_id:
        target = next((f for f in active if f.get("feeder_id") == req.feeder_id and f.get("fault_type") == "feeder"), None)
    elif active:
        target = active[-1]

    if not target:
        raise ValueError("no matching active simulated fault")

    pole_ids = target["affected_pole_ids"]
    poles = db.query(Pole).filter(Pole.id.in_(pole_ids)).all()
    msgs: list[TelemetryPayload] = []
    base = datetime.utcnow()
    for i, p in enumerate(poles):
        if not p.has_device:
            p.energized = True
            continue
        seq = p.last_seq + 1
        msgs.append(
            TelemetryPayload(
                device_id=p.device_id or f"DEV-{p.id}",
                pole_id=p.id,
                event="boot",
                energized=True,
                ts=base + timedelta(seconds=i * 0.05),
                seq=0,
                battery_mv=3800,
                rssi=random.randint(-90, -60),
                fw=p.firmware,
            )
        )
        msgs.append(
            TelemetryPayload(
                device_id=p.device_id or f"DEV-{p.id}",
                pole_id=p.id,
                event="power_restored",
                energized=True,
                ts=base + timedelta(seconds=1 + i * 0.05),
                seq=1,
                battery_mv=3800,
                rssi=random.randint(-90, -60),
                fw=p.firmware,
            )
        )
    # Reset seq handling: boot resets — ingest accepts boot then power_restored
    for p in poles:
        p.last_seq = 0
    db.commit()

    ingest_result = ingest_batch(db, msgs)
    for p in poles:
        p.energized = True
        p.device_offline = False
        p.sensor_suspect = False
    db.commit()
    tickets = apply_localization(db, dt_ids=list({p.dt_id for p in poles}))

    active = [f for f in active if f["id"] != target["id"]]
    state.active_faults_json = json.dumps(active)
    db.commit()

    return {
        "repaired": target,
        "telemetry_sent": len(msgs),
        "ingest": ingest_result,
        "localization_touched_tickets": len(tickets),
    }


def kill_device(db: Session, pole_id: str) -> dict[str, Any]:
    """Device dies while power is fine — should NOT create a fault ticket."""
    pole = db.get(Pole, pole_id)
    if not pole:
        raise ValueError("unknown pole")
    if not pole.has_device:
        raise ValueError("pole has no device")
    pole.device_offline = True
    # last known energized stays True — silence with live last state
    pole.last_seen_at = datetime.utcnow() - timedelta(minutes=45)
    state = _sim_state(db)
    dead = _load_json(state.dead_devices_json)
    dead.append({"pole_id": pole_id, "at": datetime.utcnow().isoformat() + "Z"})
    state.dead_devices_json = json.dumps(dead)
    db.commit()
    # Run localization to prove no ticket
    before = apply_localization(db, dt_ids=[pole.dt_id])
    return {
        "pole_id": pole_id,
        "device_offline": True,
        "energized_last_known": pole.energized,
        "tickets_created": [t.id for t in before],
        "note": "Silence with last-known-live must not open a fault ticket.",
    }


def revive_device(db: Session, pole_id: str) -> dict[str, Any]:
    pole = db.get(Pole, pole_id)
    if not pole:
        raise ValueError("unknown pole")
    pole.device_offline = False
    pole.last_seen_at = datetime.utcnow()
    seq = pole.last_seq + 1
    msg = TelemetryPayload(
        device_id=pole.device_id or f"DEV-{pole.id}",
        pole_id=pole.id,
        event="heartbeat",
        energized=True,
        ts=datetime.utcnow(),
        seq=seq,
        battery_mv=3900,
        rssi=-75,
        fw=pole.firmware,
    )
    from app.services.ingest import ingest_one

    r = ingest_one(db, msg)
    state = _sim_state(db)
    dead = [d for d in _load_json(state.dead_devices_json) if d.get("pole_id") != pole_id]
    state.dead_devices_json = json.dumps(dead)
    db.commit()
    return {"pole_id": pole_id, "ingest": r}


def activate_scheduled_demo(db: Session, dt_id: str | None = None) -> dict[str, Any]:
    """Create a current scheduled outage and darken poles — must not ticket.

    Poles are restored afterward so the demo does not leave the subdivision dirty;
    the scheduled-outage row remains as evidence.
    """
    if not dt_id:
        dt_id = db.query(DistributionTransformer).first().id
    now = datetime.utcnow()
    so_id = f"SO-DEMO-{now.strftime('%H%M%S')}"
    so = ScheduledOutage(
        id=so_id,
        scope="dt",
        target_id=dt_id,
        start=now - timedelta(minutes=10),
        end=now + timedelta(hours=1),
        reason="Load shedding (simulator)",
    )
    db.add(so)
    db.commit()
    poles = _affected_from_dt(db, dt_id)
    msgs = _make_power_lost_messages(poles, success_rate=0.9, include_duplicates=False, shuffle_order=True)
    ingest_batch(db, msgs)
    _force_dark(db, poles)
    tickets = apply_localization(db, dt_ids=[dt_id])
    ticket_ids = [t.id for t in tickets]
    # Restore physical power and cancel the demo outage so later injects still work
    for p in poles:
        p.energized = True
        p.device_offline = False
    so.cancelled = True
    db.commit()
    apply_localization(db, dt_ids=[dt_id])
    return {
        "scheduled_outage": so_id,
        "dt_id": dt_id,
        "tickets_created": ticket_ids,
        "note": "While SO was active, dark poles created zero tickets. Poles restored; demo SO cancelled.",
    }


def list_sim_state(db: Session) -> dict[str, Any]:
    state = _sim_state(db)
    return {
        "active_faults": _load_json(state.active_faults_json),
        "dead_devices": _load_json(state.dead_devices_json),
    }


def pick_demo_span(db: Session) -> dict[str, Any]:
    """Help the UI: suggest a good span fault target with known topology if possible."""
    # Prefer recorded topology DT with a mid-line instrumented pole that has descendants
    dts = (
        db.query(DistributionTransformer)
        .filter(DistributionTransformer.has_recorded_topology.is_(True))
        .all()
    )
    random.shuffle(dts)
    for dt in dts:
        by_id, kids = _kids_for_dt(db, dt.id)
        for pid, pole in by_id.items():
            desc = descendants(pid, kids)
            if pole.has_device and pole.effective_parent_id and len(desc) >= 5:
                return {
                    "downstream_pole_id": pid,
                    "dt_id": dt.id,
                    "feeder_id": dt.feeder_id,
                    "topology_source": "recorded",
                    "downstream_count": len(desc) + 1,
                }
    # Fallback any
    pole = db.query(Pole).filter(Pole.effective_parent_id.isnot(None)).first()
    return {
        "downstream_pole_id": pole.id if pole else None,
        "dt_id": pole.dt_id if pole else None,
        "feeder_id": pole.feeder_id if pole else None,
        "topology_source": pole.topology_source if pole else None,
    }
