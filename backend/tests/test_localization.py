"""Localization correctness tests — the assignment's primary test surface."""

from datetime import datetime, timedelta

from app.models.db import (
    Base,
    DistributionTransformer,
    Feeder,
    Pole,
    ScheduledOutage,
    SessionLocal,
    Ticket,
    engine,
)
from app.services.localization import apply_localization, localize_dt
from app.services.topology import PoleNode, infer_parents


def setup_module():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def _make_line(db, *, recorded=True, dt_id="D-TEST01", feeder_id="F-99-01"):
    db.add(Feeder(id=feeder_id, substation_id="SS-99", name="Test"))
    db.add(
        DistributionTransformer(
            id=dt_id,
            feeder_id=feeder_id,
            lat=12.97,
            lon=77.59,
            capacity_kva=250,
            households_served=200,
            has_recorded_topology=recorded,
        )
    )
    # DT -- P1 -- P2 -- P3 -- P4
    #              \
    #               P5 -- P6
    coords = {
        "P1": (12.9702, 77.5900),
        "P2": (12.9705, 77.5900),
        "P3": (12.9708, 77.5900),
        "P4": (12.9711, 77.5900),
        "P5": (12.9705, 77.5904),
        "P6": (12.9705, 77.5908),
    }
    parents = {"P1": None, "P2": "P1", "P3": "P2", "P4": "P3", "P5": "P2", "P6": "P5"}
    for i, (pid, (lat, lon)) in enumerate(coords.items(), start=1):
        db.add(
            Pole(
                id=pid,
                lat=lat,
                lon=lon,
                feeder_id=feeder_id,
                dt_id=dt_id,
                seq_on_line=i if recorded else None,
                parent_pole_id=parents[pid] if recorded else None,
                effective_parent_id=parents[pid],
                topology_source="recorded" if recorded else "inferred",
                ward="W-001",
                pincode="560078",
                device_id=f"DEV-{pid}",
                has_device=True,
                firmware="1.4.2",
                energized=True,
                last_seq=10,
                last_seen_at=datetime.utcnow(),
            )
        )
    db.commit()
    return dt_id


def test_span_fault_localizes_to_correct_edge():
    setup_module()
    db = SessionLocal()
    dt_id = _make_line(db)
    # Fault on span P2→P3: P3,P4 dark; P5,P6 still live (branch off P2)
    for pid in ["P3", "P4"]:
        p = db.get(Pole, pid)
        p.energized = False
    db.commit()

    dt = db.get(DistributionTransformer, dt_id)
    result = localize_dt(db, dt)
    assert len(result.faults) == 1
    f = result.faults[0]
    assert f.fault_type == "span"
    assert f.upstream_pole_id == "P2"
    assert f.downstream_pole_id == "P3"
    assert set(f.affected_pole_ids) == {"P3", "P4"}
    assert f.pincode == "560078"
    db.close()


def test_one_ticket_not_one_per_dark_pole():
    setup_module()
    db = SessionLocal()
    dt_id = _make_line(db)
    for pid in ["P3", "P4"]:
        db.get(Pole, pid).energized = False
    db.commit()
    created = apply_localization(db, dt_ids=[dt_id])
    assert len(created) == 1
    # Second run should not create another open ticket
    created2 = apply_localization(db, dt_ids=[dt_id])
    assert created2 == []
    open_count = db.query(Ticket).filter(Ticket.status == "detected").count()
    assert open_count == 1
    db.close()


def test_dt_fault_when_all_dark():
    setup_module()
    db = SessionLocal()
    dt_id = _make_line(db)
    for p in db.query(Pole).filter(Pole.dt_id == dt_id):
        p.energized = False
    db.commit()
    result = localize_dt(db, db.get(DistributionTransformer, dt_id))
    assert len(result.faults) == 1
    assert result.faults[0].fault_type == "dt"
    db.close()


def test_isolated_dark_with_live_children_is_sensor_not_fault():
    setup_module()
    db = SessionLocal()
    dt_id = _make_line(db)
    # P2 dark but P3,P4,P5,P6 live — physically impossible as line fault
    db.get(Pole, "P2").energized = False
    db.commit()
    result = localize_dt(db, db.get(DistributionTransformer, dt_id))
    assert result.faults == []
    assert "P2" in result.sensor_suspects
    db.close()


def test_scheduled_outage_suppresses_ticket():
    setup_module()
    db = SessionLocal()
    dt_id = _make_line(db)
    now = datetime.utcnow()
    db.add(
        ScheduledOutage(
            id="SO-T",
            scope="dt",
            target_id=dt_id,
            start=now - timedelta(minutes=5),
            end=now + timedelta(hours=1),
            reason="Load shedding",
        )
    )
    for p in db.query(Pole).filter(Pole.dt_id == dt_id):
        p.energized = False
    db.commit()
    result = localize_dt(db, db.get(DistributionTransformer, dt_id), now=now)
    assert result.faults == []
    assert result.suppressed_scheduled
    db.close()


def test_dead_modem_silence_does_not_count_as_dark():
    setup_module()
    db = SessionLocal()
    dt_id = _make_line(db)
    p = db.get(Pole, "P4")
    p.device_offline = True
    p.energized = True  # last known live
    db.commit()
    result = localize_dt(db, db.get(DistributionTransformer, dt_id))
    assert result.faults == []
    db.close()


def test_infer_parents_recovers_line_order():
    nodes = [
        PoleNode("A", 12.9702, 77.59),
        PoleNode("B", 12.9705, 77.59),
        PoleNode("C", 12.9708, 77.59),
        PoleNode("D", 12.9711, 77.59),
    ]
    parents = infer_parents(12.97, 77.59, nodes)
    assert parents["A"] is None
    assert parents["B"] == "A"
    assert parents["C"] == "B"
    assert parents["D"] == "C"


def test_restoration_auto_verifies_ticket():
    setup_module()
    db = SessionLocal()
    dt_id = _make_line(db)
    for pid in ["P3", "P4"]:
        db.get(Pole, pid).energized = False
    db.commit()
    created = apply_localization(db, dt_ids=[dt_id])
    assert len(created) == 1
    tid = created[0].id
    for pid in ["P3", "P4"]:
        db.get(Pole, pid).energized = True
    db.commit()
    apply_localization(db, dt_ids=[dt_id])
    t = db.get(Ticket, tid)
    assert t.status == "verified"
    db.close()
