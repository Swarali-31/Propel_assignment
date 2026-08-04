from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.db import (
    DistributionTransformer,
    Pole,
    ScheduledOutage,
    Ticket,
    TicketStatus,
    get_db,
)
from app.services.ai_briefing import generate_briefing
from app.services.ingest import TelemetryPayload, ingest_batch, ingest_one
from app.services.simulator import (
    InjectFaultRequest,
    KillDeviceRequest,
    RepairFaultRequest,
    activate_scheduled_demo,
    inject_fault,
    kill_device,
    list_sim_state,
    pick_demo_span,
    repair_fault,
    revive_device,
)
from app.services.tickets import TicketError, transition

router = APIRouter()


# ---------- Telemetry ----------


@router.post("/telemetry")
def post_telemetry(payload: TelemetryPayload, db: Session = Depends(get_db)):
    return ingest_one(db, payload)


@router.post("/telemetry/batch")
def post_telemetry_batch(payloads: list[TelemetryPayload], db: Session = Depends(get_db)):
    return ingest_batch(db, payloads)


# ---------- Network ----------


@router.get("/network/stats")
def network_stats(db: Session = Depends(get_db)):
    poles = db.query(Pole).count()
    dts = db.query(DistributionTransformer).count()
    recorded = db.query(DistributionTransformer).filter(DistributionTransformer.has_recorded_topology.is_(True)).count()
    with_device = db.query(Pole).filter(Pole.has_device.is_(True)).count()
    dark = db.query(Pole).filter(Pole.energized.is_(False)).count()
    offline = db.query(Pole).filter(Pole.device_offline.is_(True)).count()
    open_tickets = (
        db.query(Ticket)
        .filter(Ticket.status.notin_([TicketStatus.CLOSED.value, TicketStatus.VERIFIED.value]))
        .count()
    )
    return {
        "poles": poles,
        "dts": dts,
        "feeders": db.query(Pole.feeder_id).distinct().count(),
        "recorded_topology_dts": recorded,
        "inferred_topology_dts": dts - recorded,
        "poles_with_device": with_device,
        "device_coverage": round(with_device / poles, 3) if poles else 0,
        "currently_dark": dark,
        "devices_offline": offline,
        "open_tickets": open_tickets,
    }


@router.get("/network/poles")
def list_poles(
    dt_id: str | None = None,
    feeder_id: str | None = None,
    dark_only: bool = False,
    limit: int = Query(5000, le=20000),
    db: Session = Depends(get_db),
):
    q = db.query(Pole)
    if dt_id:
        q = q.filter(Pole.dt_id == dt_id)
    if feeder_id:
        q = q.filter(Pole.feeder_id == feeder_id)
    if dark_only:
        q = q.filter(Pole.energized.is_(False))
    rows = q.limit(limit).all()
    return [
        {
            "id": p.id,
            "lat": p.lat,
            "lon": p.lon,
            "dt_id": p.dt_id,
            "feeder_id": p.feeder_id,
            "parent": p.effective_parent_id,
            "energized": p.energized,
            "has_device": p.has_device,
            "device_offline": p.device_offline,
            "sensor_suspect": p.sensor_suspect,
            "topology_source": p.topology_source,
            "pincode": p.pincode,
        }
        for p in rows
    ]


@router.get("/network/dts")
def list_dts(db: Session = Depends(get_db)):
    rows = db.query(DistributionTransformer).all()
    return [
        {
            "id": d.id,
            "feeder_id": d.feeder_id,
            "lat": d.lat,
            "lon": d.lon,
            "households_served": d.households_served,
            "has_recorded_topology": d.has_recorded_topology,
            "capacity_kva": d.capacity_kva,
        }
        for d in rows
    ]


@router.get("/network/edges")
def network_edges(dt_id: str | None = None, limit_dts: int = 12, db: Session = Depends(get_db)):
    """Return parent→child edges for map drawing (subset if no dt_id)."""
    if dt_id:
        poles = db.query(Pole).filter(Pole.dt_id == dt_id).all()
    else:
        # poles under open tickets' DTs, else first N DTs
        open_t = (
            db.query(Ticket)
            .filter(Ticket.status.notin_([TicketStatus.CLOSED.value]))
            .limit(20)
            .all()
        )
        dt_ids = {t.dt_id for t in open_t if t.dt_id}
        if not dt_ids:
            dt_ids = {d.id for d in db.query(DistributionTransformer).limit(limit_dts).all()}
        poles = db.query(Pole).filter(Pole.dt_id.in_(list(dt_ids))).all()

    by_id = {p.id: p for p in poles}
    edges = []
    for p in poles:
        if p.effective_parent_id and p.effective_parent_id in by_id:
            parent = by_id[p.effective_parent_id]
            edges.append(
                {
                    "from": parent.id,
                    "to": p.id,
                    "a": [parent.lat, parent.lon],
                    "b": [p.lat, p.lon],
                    "dt_id": p.dt_id,
                    "dark": (not parent.energized) or (not p.energized),
                }
            )
    return {"edges": edges, "pole_count": len(poles)}


# ---------- Tickets ----------


@router.get("/tickets")
def list_tickets(
    status: str | None = None,
    include_closed: bool = False,
    db: Session = Depends(get_db),
):
    q = db.query(Ticket).order_by(Ticket.created_at.desc())
    if status:
        q = q.filter(Ticket.status == status)
    elif not include_closed:
        q = q.filter(Ticket.status != TicketStatus.CLOSED.value)
    return [_ticket_dict(t) for t in q.limit(200).all()]


@router.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: str, db: Session = Depends(get_db)):
    t = db.get(Ticket, ticket_id)
    if not t:
        raise HTTPException(404, "Ticket not found")
    return _ticket_dict(t)


class TicketAction(BaseModel):
    action: str
    crew_name: str | None = None
    notes: str | None = None


@router.post("/tickets/{ticket_id}/actions")
def ticket_action(ticket_id: str, body: TicketAction, db: Session = Depends(get_db)):
    try:
        t = transition(db, ticket_id, body.action, crew_name=body.crew_name, notes=body.notes)
        return _ticket_dict(t)
    except TicketError as e:
        status = 404 if e.code == "not_found" else 409
        raise HTTPException(status, detail={"message": str(e), "code": e.code}) from e


@router.post("/tickets/{ticket_id}/briefing")
async def ticket_briefing(ticket_id: str, db: Session = Depends(get_db)):
    result = await generate_briefing(db, ticket_id)
    if not result.get("ok"):
        raise HTTPException(404, result.get("error", "error"))
    return result


def _ticket_dict(t: Ticket) -> dict:
    import json

    return {
        "id": t.id,
        "status": t.status,
        "fault_type": t.fault_type,
        "feeder_id": t.feeder_id,
        "dt_id": t.dt_id,
        "upstream_pole_id": t.upstream_pole_id,
        "downstream_pole_id": t.downstream_pole_id,
        "asset_label": t.asset_label,
        "lat": t.lat,
        "lon": t.lon,
        "pincode": t.pincode,
        "affected_pole_count": t.affected_pole_count,
        "households_estimate": t.households_estimate,
        "confidence": t.confidence,
        "confidence_reason": t.confidence_reason,
        "topology_source": t.topology_source,
        "affected_pole_ids": json.loads(t.affected_pole_ids or "[]"),
        "crew_name": t.crew_name,
        "operator_notes": t.operator_notes,
        "ai_briefing": t.ai_briefing,
        "false_resolve_attempts": t.false_resolve_attempts,
        "created_at": t.created_at.isoformat() + "Z" if t.created_at else None,
        "updated_at": t.updated_at.isoformat() + "Z" if t.updated_at else None,
        "verified_at": t.verified_at.isoformat() + "Z" if t.verified_at else None,
        "detected_at": t.detected_at.isoformat() + "Z" if t.detected_at else None,
    }


# ---------- Scheduled outages ----------


@router.get("/scheduled-outages")
def scheduled_outages(
    from_ts: datetime | None = Query(None, alias="from"),
    to_ts: datetime | None = Query(None, alias="to"),
    db: Session = Depends(get_db),
):
    q = db.query(ScheduledOutage)
    if from_ts:
        q = q.filter(ScheduledOutage.end >= from_ts)
    if to_ts:
        q = q.filter(ScheduledOutage.start <= to_ts)
    return [
        {
            "id": s.id,
            "scope": s.scope,
            "target_id": s.target_id,
            "start": s.start.isoformat() + "Z",
            "end": s.end.isoformat() + "Z",
            "reason": s.reason,
            "cancelled": s.cancelled,
        }
        for s in q.all()
    ]


# ---------- Simulator ----------


@router.get("/simulator/state")
def sim_state(db: Session = Depends(get_db)):
    return list_sim_state(db)


@router.get("/simulator/suggest-span")
def sim_suggest(db: Session = Depends(get_db)):
    return pick_demo_span(db)


@router.post("/simulator/inject")
def sim_inject(body: InjectFaultRequest, db: Session = Depends(get_db)):
    try:
        return inject_fault(db, body)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/simulator/repair")
def sim_repair(body: RepairFaultRequest, db: Session = Depends(get_db)):
    try:
        return repair_fault(db, body)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/simulator/kill-device")
def sim_kill(body: KillDeviceRequest, db: Session = Depends(get_db)):
    try:
        return kill_device(db, body.pole_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/simulator/revive-device")
def sim_revive(body: KillDeviceRequest, db: Session = Depends(get_db)):
    try:
        return revive_device(db, body.pole_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/simulator/scheduled-outage-demo")
def sim_scheduled(dt_id: str | None = None, db: Session = Depends(get_db)):
    return activate_scheduled_demo(db, dt_id=dt_id)


@router.post("/simulator/reset")
def sim_reset(db: Session = Depends(get_db)):
    """Restore all poles to live, clear sim faults, close open tickets. Demo hygiene."""
    from app.models.db import SimulatorState, Ticket, TicketStatus
    from datetime import datetime

    now = datetime.utcnow()
    for p in db.query(Pole).all():
        p.energized = True
        p.device_offline = False
        p.sensor_suspect = False
        p.last_seen_at = now
    for t in db.query(Ticket).filter(Ticket.status != TicketStatus.CLOSED.value).all():
        t.status = TicketStatus.CLOSED.value
        t.closed_at = now
        t.updated_at = now
        t.operator_notes = (t.operator_notes + "\n" if t.operator_notes else "") + "Closed by simulator reset."
    state = db.get(SimulatorState, 1)
    if state:
        state.active_faults_json = "[]"
        state.dead_devices_json = "[]"
    db.commit()
    return {"ok": True, "message": "Network restored; open tickets closed."}
