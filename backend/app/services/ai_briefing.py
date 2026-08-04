"""Operator-facing AI briefing. Localization stays deterministic; the LLM only
translates structured fault facts into plain language for a 2 a.m. control room.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.db import Ticket


def _template_briefing(ticket: Ticket) -> str:
    poles = json.loads(ticket.affected_pole_ids or "[]")
    topo = (
        "Wiring order is from the asset register."
        if ticket.topology_source == "recorded"
        else "Wiring order was inferred from GPS — treat the exact span as a best estimate and walk the line if the break is not at the reported point."
    )
    pin = ticket.pincode or "PIN unknown — use coordinates"
    return (
        f"FAULT BRIEF — {ticket.id}\n"
        f"Type: {ticket.fault_type.upper()} on {ticket.asset_label}\n"
        f"Drive to: {ticket.lat:.5f}° N, {ticket.lon:.5f}° E · PIN {pin}\n"
        f"Feeder {ticket.feeder_id}"
        + (f" · DT {ticket.dt_id}" if ticket.dt_id else "")
        + f"\nAffected poles: {ticket.affected_pole_count} "
        f"(~{ticket.households_estimate} households)\n"
        f"Confidence: {ticket.confidence:.0%} — {ticket.confidence_reason}\n"
        f"{topo}\n"
        f"Do not close this ticket until pole lamps on the dark side report live again.\n"
        f"Sample affected poles: {', '.join(poles[:8])}{'…' if len(poles) > 8 else ''}"
    )


async def generate_briefing(db: Session, ticket_id: str) -> dict[str, Any]:
    ticket = db.get(Ticket, ticket_id)
    if not ticket:
        return {"ok": False, "error": "not_found"}

    if ticket.ai_briefing:
        return {"ok": True, "source": "cached", "briefing": ticket.ai_briefing}

    if not settings.openai_api_key:
        text = _template_briefing(ticket)
        ticket.ai_briefing = text
        db.commit()
        return {"ok": True, "source": "template", "briefing": text}

    poles = json.loads(ticket.affected_pole_ids or "[]")
    payload = {
        "id": ticket.id,
        "fault_type": ticket.fault_type,
        "asset": ticket.asset_label,
        "lat": ticket.lat,
        "lon": ticket.lon,
        "pincode": ticket.pincode,
        "feeder_id": ticket.feeder_id,
        "dt_id": ticket.dt_id,
        "affected_poles": ticket.affected_pole_count,
        "households": ticket.households_estimate,
        "confidence": ticket.confidence,
        "confidence_reason": ticket.confidence_reason,
        "topology_source": ticket.topology_source,
        "sample_poles": poles[:12],
    }
    prompt = (
        "You write terse briefings for electricity control-room operators in Karnataka. "
        "No fluff. 6–10 short lines. Include drive-to coordinates, PIN, asset, "
        "how many poles/households, confidence caveat, and one field action.\n\n"
        f"DATA:\n{json.dumps(payload, indent=2)}"
    )

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                json={
                    "model": settings.openai_model,
                    "messages": [
                        {"role": "system", "content": "You are a utility control-room assistant."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 350,
                },
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"].strip()
            ticket.ai_briefing = text
            db.commit()
            return {"ok": True, "source": "openai", "briefing": text, "model": settings.openai_model}
    except Exception as exc:  # noqa: BLE001
        text = _template_briefing(ticket)
        ticket.ai_briefing = text
        db.commit()
        return {
            "ok": True,
            "source": "template_fallback",
            "briefing": text,
            "error": str(exc),
        }
