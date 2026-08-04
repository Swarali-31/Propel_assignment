"""Telemetry ingest with per-device seq ordering, de-duplication, and staleness checks."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.models.db import Pole, TelemetryEvent
from app.services.localization import apply_localization


class TelemetryPayload(BaseModel):
    device_id: str
    pole_id: str
    event: str
    energized: bool
    ts: datetime
    seq: int
    battery_mv: int | None = None
    rssi: int | None = None
    fw: str | None = None


STALE_AFTER = timedelta(hours=6)


def ingest_one(db: Session, payload: TelemetryPayload, received_at: datetime | None = None) -> dict[str, Any]:
    received_at = received_at or datetime.utcnow()
    pole = db.get(Pole, payload.pole_id)
    if pole is None:
        evt = TelemetryEvent(
            device_id=payload.device_id,
            pole_id=payload.pole_id,
            event=payload.event,
            energized=payload.energized,
            ts=payload.ts,
            seq=payload.seq,
            battery_mv=payload.battery_mv,
            rssi=payload.rssi,
            fw=payload.fw,
            received_at=received_at,
            accepted=False,
            reject_reason="unknown_pole",
        )
        db.add(evt)
        db.commit()
        return {"accepted": False, "reason": "unknown_pole"}

    # Prefer pole_id for location; update device mapping if swapped
    if payload.device_id and pole.device_id != payload.device_id:
        pole.device_id = payload.device_id

    # Reject very stale retries (beyond 6h) relative to receive time using device ts
    if received_at - payload.ts > STALE_AFTER and payload.event in {"power_lost", "power_restored"}:
        # Still record but do not apply if we already have newer state
        if pole.last_event_ts and pole.last_event_ts > payload.ts:
            evt = TelemetryEvent(
                device_id=payload.device_id,
                pole_id=payload.pole_id,
                event=payload.event,
                energized=payload.energized,
                ts=payload.ts,
                seq=payload.seq,
                battery_mv=payload.battery_mv,
                rssi=payload.rssi,
                fw=payload.fw,
                received_at=received_at,
                accepted=False,
                reject_reason="stale",
            )
            db.add(evt)
            db.commit()
            return {"accepted": False, "reason": "stale"}

    # Per-device seq: ignore duplicates and older seq (except boot resets)
    if payload.event != "boot" and payload.seq < pole.last_seq:
        evt = TelemetryEvent(
            device_id=payload.device_id,
            pole_id=payload.pole_id,
            event=payload.event,
            energized=payload.energized,
            ts=payload.ts,
            seq=payload.seq,
            battery_mv=payload.battery_mv,
            rssi=payload.rssi,
            fw=payload.fw,
            received_at=received_at,
            accepted=False,
            reject_reason="old_seq",
        )
        db.add(evt)
        db.commit()
        return {"accepted": False, "reason": "old_seq"}

    if payload.event != "boot" and payload.seq == pole.last_seq and pole.last_event_ts:
        evt = TelemetryEvent(
            device_id=payload.device_id,
            pole_id=payload.pole_id,
            event=payload.event,
            energized=payload.energized,
            ts=payload.ts,
            seq=payload.seq,
            battery_mv=payload.battery_mv,
            rssi=payload.rssi,
            fw=payload.fw,
            received_at=received_at,
            accepted=False,
            reject_reason="duplicate",
        )
        db.add(evt)
        db.commit()
        return {"accepted": False, "reason": "duplicate"}

    if payload.event == "boot":
        pole.last_seq = payload.seq
    else:
        pole.last_seq = max(pole.last_seq, payload.seq)

    prev_energized = pole.energized
    if payload.event in {"power_lost"}:
        pole.energized = False
    elif payload.event in {"power_restored", "boot"}:
        pole.energized = True if payload.event == "power_restored" else payload.energized
        pole.device_offline = False
        pole.sensor_suspect = False
    elif payload.event == "heartbeat":
        pole.energized = payload.energized
        pole.device_offline = False

    if payload.energized is not None and payload.event in {"heartbeat", "power_lost", "power_restored"}:
        pole.energized = payload.energized

    pole.last_event_ts = payload.ts
    pole.last_seen_at = received_at
    if payload.fw:
        pole.firmware = payload.fw

    evt = TelemetryEvent(
        device_id=payload.device_id,
        pole_id=payload.pole_id,
        event=payload.event,
        energized=payload.energized,
        ts=payload.ts,
        seq=payload.seq,
        battery_mv=payload.battery_mv,
        rssi=payload.rssi,
        fw=payload.fw,
        received_at=received_at,
        accepted=True,
    )
    db.add(evt)
    db.commit()

    changed = prev_energized != pole.energized
    tickets = []
    if changed or payload.event in {"power_lost", "power_restored"}:
        tickets = apply_localization(db, dt_ids=[pole.dt_id])

    return {
        "accepted": True,
        "pole_id": pole.id,
        "energized": pole.energized,
        "state_changed": changed,
        "tickets_created": [t.id for t in tickets],
    }


def ingest_batch(
    db: Session,
    payloads: list[TelemetryPayload],
    *,
    run_localization: bool = True,
) -> dict[str, Any]:
    """Ingest many messages; localization runs once per affected DT at the end."""
    accepted = 0
    rejected = 0
    affected_dts: set[str] = set()
    for p in payloads:
        r = _apply_state_only(db, p)
        if r["accepted"]:
            accepted += 1
            if r.get("dt_id"):
                affected_dts.add(r["dt_id"])
        else:
            rejected += 1
    db.commit()
    created = []
    if run_localization and affected_dts:
        created = apply_localization(db, dt_ids=list(affected_dts))
    return {
        "accepted": accepted,
        "rejected": rejected,
        "tickets_created": [t.id for t in created],
        "affected_dts": list(affected_dts),
    }


def _apply_state_only(db: Session, payload: TelemetryPayload) -> dict[str, Any]:
    received_at = datetime.utcnow()
    pole = db.get(Pole, payload.pole_id)
    if pole is None:
        return {"accepted": False}

    if payload.event != "boot" and payload.seq < pole.last_seq:
        return {"accepted": False}
    if payload.event != "boot" and payload.seq == pole.last_seq:
        return {"accepted": False}

    if payload.event == "boot":
        pole.last_seq = payload.seq
    else:
        pole.last_seq = max(pole.last_seq, payload.seq)

    if payload.event == "power_lost":
        pole.energized = False
    elif payload.event in {"power_restored"}:
        pole.energized = True
        pole.device_offline = False
        pole.sensor_suspect = False
    elif payload.event == "boot":
        pole.energized = payload.energized
        pole.device_offline = False
    elif payload.event == "heartbeat":
        pole.energized = payload.energized
        pole.device_offline = False

    pole.last_event_ts = payload.ts
    pole.last_seen_at = received_at
    if payload.fw:
        pole.firmware = payload.fw
    if payload.device_id:
        pole.device_id = payload.device_id

    db.add(
        TelemetryEvent(
            device_id=payload.device_id,
            pole_id=payload.pole_id,
            event=payload.event,
            energized=payload.energized,
            ts=payload.ts,
            seq=payload.seq,
            battery_mv=payload.battery_mv,
            rssi=payload.rssi,
            fw=payload.fw,
            received_at=received_at,
            accepted=True,
        )
    )
    return {"accepted": True, "dt_id": pole.dt_id}


def mark_silent_devices(db: Session, now: datetime | None = None) -> int:
    """Flag devices that missed heartbeats while last known live as offline (not dark)."""
    now = now or datetime.utcnow()
    cutoff = now - timedelta(seconds=settings.heartbeat_timeout_seconds)
    poles = (
        db.query(Pole)
        .filter(Pole.has_device.is_(True))
        .filter(Pole.energized.is_(True))
        .filter(Pole.device_offline.is_(False))
        .filter(Pole.last_seen_at.isnot(None))
        .filter(Pole.last_seen_at < cutoff)
        .all()
    )
    for p in poles:
        p.device_offline = True
    db.commit()
    return len(poles)
