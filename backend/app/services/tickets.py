"""Ticket lifecycle. Restoration is verified from telemetry only."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.db import Pole, Ticket, TicketStatus


class TicketError(Exception):
    def __init__(self, message: str, code: str = "invalid"):
        super().__init__(message)
        self.code = code


def transition(db: Session, ticket_id: str, action: str, crew_name: str | None = None, notes: str | None = None) -> Ticket:
    t = db.get(Ticket, ticket_id)
    if not t:
        raise TicketError("Ticket not found", "not_found")

    now = datetime.utcnow()
    if notes:
        t.operator_notes = (t.operator_notes + "\n" if t.operator_notes else "") + notes

    if action == "acknowledge":
        if t.status != TicketStatus.DETECTED.value:
            raise TicketError(f"Cannot acknowledge from status {t.status}")
        t.status = TicketStatus.ACKNOWLEDGED.value
        t.acknowledged_at = now

    elif action == "assign":
        if t.status not in {TicketStatus.DETECTED.value, TicketStatus.ACKNOWLEDGED.value}:
            raise TicketError(f"Cannot assign from status {t.status}")
        t.status = TicketStatus.CREW_ASSIGNED.value
        t.crew_name = crew_name or "Crew-A"
        t.assigned_at = now

    elif action == "resolve":
        if t.status not in {
            TicketStatus.ACKNOWLEDGED.value,
            TicketStatus.CREW_ASSIGNED.value,
            TicketStatus.DETECTED.value,
        }:
            raise TicketError(f"Cannot resolve from status {t.status}")
        # Do NOT trust the click alone — check telemetry
        ids = json.loads(t.affected_pole_ids or "[]")
        poles = db.query(Pole).filter(Pole.id.in_(ids)).all() if ids else []
        instrumented = [p for p in poles if p.has_device and not p.device_offline]
        still_dark = [p.id for p in instrumented if not p.energized]
        if still_dark:
            t.false_resolve_attempts += 1
            t.updated_at = now
            db.commit()
            db.refresh(t)
            raise TicketError(
                f"Telemetry still shows {len(still_dark)} dark pole(s). "
                "Cannot mark resolved until power is measured restored.",
                "telemetry_not_restored",
            )
        t.status = TicketStatus.RESOLVED.value
        t.resolved_at = now
        # If already live, immediately verify
        if instrumented and all(p.energized for p in instrumented):
            t.status = TicketStatus.VERIFIED.value
            t.verified_at = now

    elif action == "close":
        if t.status != TicketStatus.VERIFIED.value:
            raise TicketError("Only verified tickets can be closed")
        t.status = TicketStatus.CLOSED.value
        t.closed_at = now

    else:
        raise TicketError(f"Unknown action {action}")

    t.updated_at = now
    db.commit()
    db.refresh(t)
    return t
