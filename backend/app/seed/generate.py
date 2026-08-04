"""Generate a synthetic subdivision network matching assignment proportions.

Scale (default): ~3,200 poles, ~48 DTs, ~12 feeders, 4 substations.
~60% of DTs lack recorded topology; ~9% poles have no device; ~8% on fw 1.2.x.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta
from pathlib import Path

from app.config import settings
from app.models.db import (
    DistributionTransformer,
    Feeder,
    Pole,
    ScheduledOutage,
    SessionLocal,
    SimulatorState,
    init_db,
)
from app.services.topology import PoleNode, infer_parents


# Bangalore-ish bounding box for a fictional subdivision
ORIGIN_LAT = 12.9350
ORIGIN_LON = 77.5800

PINCODES = ["560078", "560076", "560102", "560034", "560095", "560029", None]


def _jitter(scale: float = 0.0008) -> float:
    return random.uniform(-scale, scale)


def generate_network(
    n_substations: int = 4,
    feeders_per_ss: int = 3,
    dts_per_feeder: tuple[int, int] = (3, 5),
    poles_per_dt: tuple[int, int] = (35, 95),
    recorded_topology_fraction: float = 0.40,
    device_fraction: float = 0.91,
    fw12_fraction: float = 0.08,
    seed: int = 42,
) -> dict:
    random.seed(seed)
    init_db()
    db = SessionLocal()

    # Fresh seed only if empty
    if db.query(Pole).count() > 0:
        stats = {
            "poles": db.query(Pole).count(),
            "dts": db.query(DistributionTransformer).count(),
            "feeders": db.query(Feeder).count(),
            "skipped": True,
        }
        db.close()
        return stats

    feeders: list[Feeder] = []
    dts: list[DistributionTransformer] = []
    poles: list[Pole] = []
    pole_counter = 10000

    for ss in range(1, n_substations + 1):
        ss_lat = ORIGIN_LAT + (ss - 1) * 0.018 + _jitter(0.002)
        ss_lon = ORIGIN_LON + ((ss % 2) * 0.022) + _jitter(0.002)
        for fi in range(1, feeders_per_ss + 1):
            feeder_id = f"F-{ss:02d}-{fi:02d}"
            feeders.append(
                Feeder(id=feeder_id, substation_id=f"SS-{ss:02d}", name=f"Feeder {feeder_id}")
            )
            n_dt = random.randint(*dts_per_feeder)
            for di in range(1, n_dt + 1):
                dt_id = f"D-{ss:02d}{fi:02d}{di:02d}"
                angle = random.uniform(0, 2 * math.pi)
                dist = 0.004 + di * 0.0035 + random.uniform(0, 0.002)
                dt_lat = ss_lat + dist * math.cos(angle)
                dt_lon = ss_lon + dist * math.sin(angle)
                has_topo = random.random() < recorded_topology_fraction
                hh = random.randint(80, 420)
                dt = DistributionTransformer(
                    id=dt_id,
                    feeder_id=feeder_id,
                    lat=dt_lat,
                    lon=dt_lon,
                    capacity_kva=random.choice([100, 160, 250, 315]),
                    households_served=hh,
                    has_recorded_topology=has_topo,
                )
                dts.append(dt)

                n_poles = random.randint(*poles_per_dt)
                # Build a radial line with 1–3 spurs
                main_count = int(n_poles * random.uniform(0.55, 0.75))
                spur_count = n_poles - main_count
                bearing = random.uniform(0, 2 * math.pi)

                true_parents: dict[str, str | None] = {}
                pole_objs: list[Pole] = []
                main_ids: list[str] = []

                for i in range(main_count):
                    pole_counter += 1
                    pid = f"P-{pole_counter:06d}"
                    step = 0.00035 + random.uniform(-0.00005, 0.00008)
                    lat = dt_lat + (i + 1) * step * math.cos(bearing) + _jitter(0.00005)
                    lon = dt_lon + (i + 1) * step * math.sin(bearing) + _jitter(0.00005)
                    parent = main_ids[-1] if main_ids else None
                    true_parents[pid] = parent
                    main_ids.append(pid)
                    has_device = random.random() < device_fraction
                    fw = "1.2.4" if random.random() < fw12_fraction else random.choice(["1.3.1", "1.4.0", "1.4.2"])
                    pin = random.choice(PINCODES)
                    device_id = f"KSPDB-SD07-{dt_id}-{pole_counter % 10000:04d}" if has_device else None
                    pole_objs.append(
                        Pole(
                            id=pid,
                            lat=lat,
                            lon=lon,
                            feeder_id=feeder_id,
                            dt_id=dt_id,
                            seq_on_line=(i + 1) if has_topo else None,
                            parent_pole_id=parent if has_topo else None,
                            effective_parent_id=None,  # filled below
                            topology_source="recorded" if has_topo else "inferred",
                            pole_type=random.choice(["LT-9m-PCC", "LT-8m-Steel", "LT-9m-Steel"]),
                            ward=f"W-{(ss * 20 + fi * 3 + di):03d}",
                            pincode=pin,
                            device_id=device_id,
                            has_device=has_device,
                            firmware=fw if has_device else "n/a",
                            energized=True,
                            last_seq=random.randint(100, 5000),
                            last_seen_at=datetime.utcnow(),
                        )
                    )

                # Spurs off random main poles
                remaining = spur_count
                spur_idx = 0
                while remaining > 0 and main_ids:
                    branch_len = min(remaining, random.randint(3, 12))
                    attach = random.choice(main_ids[max(1, len(main_ids) // 4) :])
                    attach_pole = next(p for p in pole_objs if p.id == attach)
                    spur_bearing = bearing + random.choice([-1, 1]) * random.uniform(0.6, 1.4)
                    prev = attach
                    for j in range(branch_len):
                        pole_counter += 1
                        pid = f"P-{pole_counter:06d}"
                        step = 0.00032 + random.uniform(-0.00004, 0.00006)
                        lat = attach_pole.lat + (j + 1) * step * math.cos(spur_bearing) + _jitter(0.00004)
                        lon = attach_pole.lon + (j + 1) * step * math.sin(spur_bearing) + _jitter(0.00004)
                        true_parents[pid] = prev
                        has_device = random.random() < device_fraction
                        fw = "1.2.4" if random.random() < fw12_fraction else "1.4.2"
                        device_id = f"KSPDB-SD07-{dt_id}-{pole_counter % 10000:04d}" if has_device else None
                        seq = None
                        parent_rec = None
                        if has_topo:
                            # seq continues loosely
                            seq = main_count + spur_idx + j + 1
                            parent_rec = prev
                        pole_objs.append(
                            Pole(
                                id=pid,
                                lat=lat,
                                lon=lon,
                                feeder_id=feeder_id,
                                dt_id=dt_id,
                                seq_on_line=seq,
                                parent_pole_id=parent_rec,
                                effective_parent_id=None,
                                topology_source="recorded" if has_topo else "inferred",
                                pole_type="LT-9m-PCC",
                                ward=attach_pole.ward,
                                pincode=attach_pole.pincode,
                                device_id=device_id,
                                has_device=has_device,
                                firmware=fw if has_device else "n/a",
                                energized=True,
                                last_seq=random.randint(100, 5000),
                                last_seen_at=datetime.utcnow(),
                            )
                        )
                        prev = pid
                    remaining -= branch_len
                    spur_idx += branch_len

                # Fill effective parents
                if has_topo:
                    for p in pole_objs:
                        p.effective_parent_id = true_parents[p.id]
                        p.topology_source = "recorded"
                else:
                    inferred = infer_parents(
                        dt_lat,
                        dt_lon,
                        [PoleNode(id=p.id, lat=p.lat, lon=p.lon) for p in pole_objs],
                    )
                    for p in pole_objs:
                        p.effective_parent_id = inferred.get(p.id)
                        p.topology_source = "inferred"
                        p.seq_on_line = None
                        p.parent_pole_id = None

                poles.extend(pole_objs)

    db.add_all(feeders)
    db.add_all(dts)
    db.add_all(poles)

    # Seed a couple of scheduled outages (future / current window examples)
    now = datetime.utcnow()
    sample_feeder = feeders[0].id
    sample_dt = dts[1].id if len(dts) > 1 else dts[0].id
    db.add_all(
        [
            ScheduledOutage(
                id=f"SO-{now.strftime('%Y%m%d')}-001",
                scope="feeder",
                target_id=sample_feeder,
                start=now + timedelta(hours=6),
                end=now + timedelta(hours=8),
                reason="Planned maintenance - jumper replacement",
            ),
            # Past load-shedding window (for API examples). Active demos are
            # created via /api/simulator/scheduled-outage-demo so they don't
            # permanently suppress faults on a random DT at startup.
            ScheduledOutage(
                id=f"SO-{now.strftime('%Y%m%d')}-002",
                scope="dt",
                target_id=sample_dt,
                start=now - timedelta(hours=5),
                end=now - timedelta(hours=4),
                reason="Load shedding",
                cancelled=False,
            ),
        ]
    )
    db.add(SimulatorState(id=1, active_faults_json="[]", dead_devices_json="[]"))
    db.commit()

    stats = {
        "poles": len(poles),
        "dts": len(dts),
        "feeders": len(feeders),
        "recorded_topo_dts": sum(1 for d in dts if d.has_recorded_topology),
        "inferred_topo_dts": sum(1 for d in dts if not d.has_recorded_topology),
        "with_device": sum(1 for p in poles if p.has_device),
        "skipped": False,
    }
    db.close()

    # Also write CSV exports for inspection
    export_dir = Path(settings.data_dir) / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    return stats


if __name__ == "__main__":
    s = generate_network()
    print("Seed complete:", s)
