"""Fault localization from live/dark pole states on a radial LT network.

Core idea: sensors report node state; faults live on edges. The fault is the
frontier between the live region and the dark region in the tree.

Complexity: O(P) per DT evaluation where P is poles under that DT. We evaluate
only DTs that have recently seen a state change.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable

from sqlalchemy.orm import Session

from app.config import settings
from app.models.db import (
    DistributionTransformer,
    FaultType,
    Pole,
    ScheduledOutage,
    Ticket,
    TicketStatus,
    TopologySource,
)
from app.services.topology import children_map, descendants, haversine_m


OPEN_STATUSES = {
    TicketStatus.DETECTED.value,
    TicketStatus.ACKNOWLEDGED.value,
    TicketStatus.CREW_ASSIGNED.value,
    TicketStatus.RESOLVED.value,
}


@dataclass
class LocalizedFault:
    fault_type: str
    feeder_id: str
    dt_id: str | None
    upstream_pole_id: str | None
    downstream_pole_id: str | None
    asset_label: str
    lat: float
    lon: float
    pincode: str | None
    affected_pole_ids: list[str]
    households_estimate: int
    confidence: float
    confidence_reason: str
    topology_source: str
    grouping_key: str


@dataclass
class LocalizationResult:
    faults: list[LocalizedFault] = field(default_factory=list)
    sensor_suspects: list[str] = field(default_factory=list)
    suppressed_scheduled: list[str] = field(default_factory=list)


def _build_kids(poles: list[Pole]) -> dict[str | None, list[str]]:
    parents = {p.id: p.effective_parent_id for p in poles}
    return children_map(parents)


def _is_under_scheduled(db: Session, feeder_id: str, dt_id: str, now: datetime) -> ScheduledOutage | None:
    grace_before = timedelta(minutes=settings.scheduled_grace_before_minutes)
    grace_after = timedelta(minutes=settings.scheduled_grace_after_minutes)
    # Equivalent to start-grace <= now <= end+grace, expressed so SQLite
    # never has to subtract Python timedeltas from column expressions.
    window_start = now + grace_before
    window_end = now - grace_after
    rows = (
        db.query(ScheduledOutage)
        .filter(ScheduledOutage.cancelled.is_(False))
        .filter(ScheduledOutage.start <= window_start)
        .filter(ScheduledOutage.end >= window_end)
        .all()
    )
    for so in rows:
        if so.scope == "feeder" and so.target_id == feeder_id:
            return so
        if so.scope == "dt" and so.target_id == dt_id:
            return so
    return None


def _pole_is_observably_dark(p: Pole) -> bool:
    """Dark for localization purposes: device reports de-energized.

    Offline devices with unknown state are treated as unknown, not dark —
    silence alone must not create tickets (could be dead modem).
    """
    if not p.has_device:
        return False
    if p.device_offline and p.energized:
        # Dead modem while last known live — unknown, not dark
        return False
    return not p.energized


def _pole_is_observably_live(p: Pole) -> bool:
    if not p.has_device:
        return False
    if p.device_offline:
        return False
    return p.energized


def localize_dt(db: Session, dt: DistributionTransformer, now: datetime | None = None) -> LocalizationResult:
    now = now or datetime.utcnow()
    result = LocalizationResult()
    poles = db.query(Pole).filter(Pole.dt_id == dt.id).all()
    if not poles:
        return result

    by_id = {p.id: p for p in poles}
    kids = _build_kids(poles)
    dark_ids = {p.id for p in poles if _pole_is_observably_dark(p)}
    live_ids = {p.id for p in poles if _pole_is_observably_live(p)}

    if not dark_ids:
        return result

    # Sensor-suspect: dark pole with at least one observably live descendant
    for pid in list(dark_ids):
        desc = descendants(pid, kids)
        if any(d in live_ids for d in desc):
            result.sensor_suspects.append(pid)
            by_id[pid].sensor_suspect = True
            dark_ids.discard(pid)

    if not dark_ids:
        db.commit()
        return result

    scheduled = _is_under_scheduled(db, dt.feeder_id, dt.id, now)
    if scheduled:
        result.suppressed_scheduled.append(scheduled.id)
        return result

    topo_source = (
        TopologySource.RECORDED.value
        if dt.has_recorded_topology
        else TopologySource.INFERRED.value
    )

    instrumented = [p for p in poles if p.has_device]
    dark_instrumented = [p for p in instrumented if p.id in dark_ids]
    all_instrumented_dark = bool(instrumented) and len(dark_instrumented) == len(instrumented)
    any_live = bool(live_ids)

    # DT-level fault: every instrumented pole under DT is dark, no live signal
    if all_instrumented_dark and not any_live and len(dark_instrumented) >= max(2, int(0.5 * max(len(instrumented), 1))):
        # Could still be a feeder fault — caller aggregates
        mid_lat = sum(p.lat for p in poles) / len(poles)
        mid_lon = sum(p.lon for p in poles) / len(poles)
        pin = next((p.pincode for p in poles if p.pincode), None)
        conf, reason = _confidence(
            topo_source=topo_source,
            boundary_clear=True,
            coverage=len(instrumented) / max(len(poles), 1),
            affected=len(dark_ids),
            gaps_on_boundary=False,
            fault_type=FaultType.DT.value,
        )
        result.faults.append(
            LocalizedFault(
                fault_type=FaultType.DT.value,
                feeder_id=dt.feeder_id,
                dt_id=dt.id,
                upstream_pole_id=None,
                downstream_pole_id=None,
                asset_label=f"DT {dt.id} (HT fuse / transformer)",
                lat=dt.lat,
                lon=dt.lon,
                pincode=pin,
                affected_pole_ids=sorted(dark_ids),
                households_estimate=dt.households_served,
                confidence=conf,
                confidence_reason=reason,
                topology_source=topo_source,
                grouping_key=f"dt:{dt.id}",
            )
        )
        return result

    # Span faults: climb from each dark pole to the live/DT frontier, then group
    # by that frontier. Uninstrumented parents are pass-through — they must not
    # create extra "roots" (that was splitting one snapped wire into many tickets).
    frontiers: dict[tuple[str | None, str], set[str]] = {}
    for pid in dark_ids:
        node = pid
        shallow_dark = pid
        upstream: str | None = None
        while True:
            parent = by_id[node].effective_parent_id
            if parent is None:
                upstream = None
                break
            if parent in live_ids:
                upstream = parent
                break
            if parent in dark_ids:
                shallow_dark = parent
            # parent unknown or dark: keep climbing
            node = parent
        # Downstream anchor = shallowest dark instrumented pole on the path
        # (the first dark pole below the live upstream / DT).
        key = (upstream, shallow_dark)
        frontiers.setdefault(key, set()).add(pid)

    # Merge components whose dark sets nest (spur artifacts)
    items = sorted(frontiers.items(), key=lambda kv: len(kv[1]), reverse=True)
    unique_comps: list[tuple[tuple[str | None, str], set[str]]] = []
    for key, comp in items:
        if any(comp <= c for _, c in unique_comps):
            continue
        merged = False
        for i, (k2, c2) in enumerate(unique_comps):
            if comp & c2:
                unique_comps[i] = (k2 if len(c2) >= len(comp) else key, c2 | comp)
                merged = True
                break
        if not merged:
            unique_comps.append((key, comp))

    for (boundary_up, boundary_down), comp in unique_comps:
        if not comp:
            continue

        down = by_id[boundary_down]
        if boundary_up and boundary_up in by_id:
            up = by_id[boundary_up]
            lat = (up.lat + down.lat) / 2
            lon = (up.lon + down.lon) / 2
            label = f"Span {boundary_up} -> {boundary_down}"
            gaps = (not up.has_device) or (not down.has_device)
        else:
            lat = (dt.lat + down.lat) / 2
            lon = (dt.lon + down.lon) / 2
            label = f"Span {dt.id} -> {boundary_down}"
            gaps = not down.has_device
            boundary_up = None

        pin = down.pincode or next((by_id[p].pincode for p in comp if by_id[p].pincode), None)
        hh = max(1, int(dt.households_served * len(comp) / max(len(poles), 1)))
        conf, reason = _confidence(
            topo_source=topo_source,
            boundary_clear=boundary_up is not None and boundary_up in live_ids,
            coverage=len(instrumented) / max(len(poles), 1),
            affected=len(comp),
            gaps_on_boundary=gaps,
            fault_type=FaultType.SPAN.value,
        )
        result.faults.append(
            LocalizedFault(
                fault_type=FaultType.SPAN.value,
                feeder_id=dt.feeder_id,
                dt_id=dt.id,
                upstream_pole_id=boundary_up,
                downstream_pole_id=boundary_down,
                asset_label=label,
                lat=lat,
                lon=lon,
                pincode=pin,
                affected_pole_ids=sorted(comp),
                households_estimate=hh,
                confidence=conf,
                confidence_reason=reason,
                topology_source=topo_source,
                grouping_key=f"span:{dt.id}:{boundary_up or 'DT'}:{boundary_down}",
            )
        )

    return result


def _confidence(
    *,
    topo_source: str,
    boundary_clear: bool,
    coverage: float,
    affected: int,
    gaps_on_boundary: bool,
    fault_type: str,
) -> tuple[float, str]:
    reasons: list[str] = []
    score = 0.55

    if topo_source == TopologySource.RECORDED.value:
        score += 0.22
        reasons.append("recorded wiring diagram")
    else:
        score -= 0.05
        reasons.append("geometry-inferred topology (±error on parallel laterals)")

    if boundary_clear:
        score += 0.12
        reasons.append("clear live/dark boundary on instrumented poles")
    else:
        score -= 0.08
        reasons.append("boundary against unknown/uninstrumented upstream")

    if coverage >= 0.9:
        score += 0.08
        reasons.append(f"high device coverage ({coverage:.0%})")
    elif coverage < 0.75:
        score -= 0.1
        reasons.append(f"gaps in device coverage ({coverage:.0%})")

    if gaps_on_boundary:
        score -= 0.12
        reasons.append("boundary pole(s) lack devices — reporting span range")

    if affected >= 3:
        score += 0.05
        reasons.append(f"{affected} corroborating dark poles")
    elif affected == 1 and fault_type == FaultType.SPAN.value:
        score -= 0.15
        reasons.append("single dark pole — weak corroboration")

    score = max(0.15, min(0.97, score))
    return score, "; ".join(reasons)


def localize_feeder_rollups(db: Session, dt_faults: list[LocalizedFault]) -> list[LocalizedFault]:
    """If every DT on a feeder reports a DT-level fault, collapse to one feeder fault."""
    from collections import defaultdict

    by_feeder: dict[str, list[LocalizedFault]] = defaultdict(list)
    for f in dt_faults:
        if f.fault_type == FaultType.DT.value:
            by_feeder[f.feeder_id].append(f)

    rollups: list[LocalizedFault] = []
    consumed_keys: set[str] = set()

    for feeder_id, faults in by_feeder.items():
        dt_ids = {d.id for d in db.query(DistributionTransformer).filter(DistributionTransformer.feeder_id == feeder_id).all()}
        faulted = {f.dt_id for f in faults}
        if dt_ids and faulted >= dt_ids and len(dt_ids) >= 2:
            all_poles = []
            for f in faults:
                all_poles.extend(f.affected_pole_ids)
            lat = sum(f.lat for f in faults) / len(faults)
            lon = sum(f.lon for f in faults) / len(faults)
            pin = next((f.pincode for f in faults if f.pincode), None)
            hh = sum(f.households_estimate for f in faults)
            rollups.append(
                LocalizedFault(
                    fault_type=FaultType.FEEDER.value,
                    feeder_id=feeder_id,
                    dt_id=None,
                    upstream_pole_id=None,
                    downstream_pole_id=None,
                    asset_label=f"11 kV feeder {feeder_id}",
                    lat=lat,
                    lon=lon,
                    pincode=pin,
                    affected_pole_ids=sorted(set(all_poles)),
                    households_estimate=hh,
                    confidence=min(0.95, faults[0].confidence + 0.05),
                    confidence_reason="all DTs on feeder dark simultaneously; " + faults[0].confidence_reason,
                    topology_source=faults[0].topology_source,
                    grouping_key=f"feeder:{feeder_id}",
                )
            )
            for f in faults:
                consumed_keys.add(f.grouping_key)

    kept = [f for f in dt_faults if f.grouping_key not in consumed_keys]
    return kept + rollups


def apply_localization(
    db: Session,
    dt_ids: Iterable[str] | None = None,
    now: datetime | None = None,
) -> list[Ticket]:
    """Run localization and upsert open tickets. Returns newly created tickets."""
    now = now or datetime.utcnow()
    q = db.query(DistributionTransformer)
    if dt_ids is not None:
        ids = list(dt_ids)
        if not ids:
            return []
        q = q.filter(DistributionTransformer.id.in_(ids))
    dts = q.all()

    all_faults: list[LocalizedFault] = []
    for dt in dts:
        res = localize_dt(db, dt, now=now)
        for sid in res.sensor_suspects:
            pole = db.get(Pole, sid)
            if pole:
                pole.sensor_suspect = True
        all_faults.extend(res.faults)

    all_faults = localize_feeder_rollups(db, all_faults)
    created: list[Ticket] = []

    active_keys = {f.grouping_key for f in all_faults}
    # DT/feeder faults supersede span tickets under the same asset
    dt_fault_ids = {f.dt_id for f in all_faults if f.fault_type == FaultType.DT.value and f.dt_id}
    feeder_fault_ids = {f.feeder_id for f in all_faults if f.fault_type == FaultType.FEEDER.value}

    for fault in all_faults:
        existing = db.query(Ticket).filter(Ticket.grouping_key == fault.grouping_key).first()
        if existing and existing.status in OPEN_STATUSES | {TicketStatus.VERIFIED.value}:
            # Refresh measurements on open ticket
            if existing.status != TicketStatus.VERIFIED.value:
                existing.affected_pole_count = len(fault.affected_pole_ids)
                existing.affected_pole_ids = json.dumps(fault.affected_pole_ids)
                existing.confidence = fault.confidence
                existing.confidence_reason = fault.confidence_reason
                existing.updated_at = now
            continue
        if existing and existing.status == TicketStatus.CLOSED.value:
            # New outage on same span — new ticket with suffix
            fault.grouping_key = f"{fault.grouping_key}:{int(now.timestamp())}"

        ticket_id = f"T-{now.strftime('%Y%m%d')}-{abs(hash(fault.grouping_key)) % 10_000_000:07d}"
        t = Ticket(
            id=ticket_id,
            status=TicketStatus.DETECTED.value,
            fault_type=fault.fault_type,
            feeder_id=fault.feeder_id,
            dt_id=fault.dt_id,
            upstream_pole_id=fault.upstream_pole_id,
            downstream_pole_id=fault.downstream_pole_id,
            asset_label=fault.asset_label,
            lat=fault.lat,
            lon=fault.lon,
            pincode=fault.pincode,
            affected_pole_count=len(fault.affected_pole_ids),
            households_estimate=fault.households_estimate,
            confidence=fault.confidence,
            confidence_reason=fault.confidence_reason,
            topology_source=fault.topology_source,
            affected_pole_ids=json.dumps(fault.affected_pole_ids),
            grouping_key=fault.grouping_key,
            detected_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(t)
        created.append(t)

    # Close superseded open span tickets when a DT/feeder ticket covers them
    if dt_fault_ids or feeder_fault_ids:
        for stale in db.query(Ticket).filter(Ticket.status.in_(list(OPEN_STATUSES))).all():
            if stale.fault_type != FaultType.SPAN.value:
                continue
            if stale.dt_id in dt_fault_ids or stale.feeder_id in feeder_fault_ids:
                stale.status = TicketStatus.CLOSED.value
                stale.closed_at = now
                stale.operator_notes = (
                    (stale.operator_notes + "\n" if stale.operator_notes else "")
                    + "Superseded by DT/feeder-level fault ticket."
                )
                stale.updated_at = now

    # Auto-verify: open tickets whose affected poles are all live again
    open_tickets = db.query(Ticket).filter(Ticket.status.in_(list(OPEN_STATUSES))).all()
    for t in open_tickets:
        # Skip if still actively faulted
        if t.grouping_key in active_keys or any(
            t.grouping_key.startswith(k.split(":")[0]) and False for k in active_keys
        ):
            # more precise: if any affected pole still dark
            pass
        ids = json.loads(t.affected_pole_ids or "[]")
        if not ids:
            continue
        poles = db.query(Pole).filter(Pole.id.in_(ids)).all()
        instrumented = [p for p in poles if p.has_device and not p.device_offline]
        if not instrumented:
            continue
        if all(p.energized for p in instrumented):
            # Restoration observed
            if t.status == TicketStatus.RESOLVED.value:
                t.status = TicketStatus.VERIFIED.value
                t.verified_at = now
                t.updated_at = now
            elif t.status in {
                TicketStatus.DETECTED.value,
                TicketStatus.ACKNOWLEDGED.value,
                TicketStatus.CREW_ASSIGNED.value,
            }:
                # Self-healed / restored without formal resolve click
                t.status = TicketStatus.VERIFIED.value
                t.resolved_at = now
                t.verified_at = now
                t.updated_at = now
                t.operator_notes = (t.operator_notes + "\n").lstrip() + "Auto-verified from telemetry restoration."

    db.commit()
    for t in created:
        db.refresh(t)
    return created
