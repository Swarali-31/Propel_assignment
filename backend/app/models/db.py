from datetime import datetime
from enum import Enum

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


class TicketStatus(str, Enum):
    DETECTED = "detected"
    ACKNOWLEDGED = "acknowledged"
    CREW_ASSIGNED = "crew_assigned"
    RESOLVED = "resolved"
    VERIFIED = "verified"
    CLOSED = "closed"


class FaultType(str, Enum):
    SPAN = "span"
    DT = "dt"
    FEEDER = "feeder"


class TopologySource(str, Enum):
    RECORDED = "recorded"
    INFERRED = "inferred"


class Feeder(Base):
    __tablename__ = "feeders"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    substation_id: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(64))


class DistributionTransformer(Base):
    __tablename__ = "distribution_transformers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    feeder_id: Mapped[str] = mapped_column(String(32), ForeignKey("feeders.id"), index=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    capacity_kva: Mapped[int] = mapped_column(Integer)
    households_served: Mapped[int] = mapped_column(Integer)
    has_recorded_topology: Mapped[bool] = mapped_column(Boolean, default=False)

    poles: Mapped[list["Pole"]] = relationship(back_populates="dt")


class Pole(Base):
    __tablename__ = "poles"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    feeder_id: Mapped[str] = mapped_column(String(32), index=True)
    dt_id: Mapped[str] = mapped_column(String(32), ForeignKey("distribution_transformers.id"), index=True)
    seq_on_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parent_pole_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # Effective topology used by localization (recorded or inferred)
    effective_parent_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    topology_source: Mapped[str] = mapped_column(String(16), default=TopologySource.INFERRED.value)
    pole_type: Mapped[str] = mapped_column(String(32), default="LT-9m-PCC")
    ward: Mapped[str] = mapped_column(String(16))
    pincode: Mapped[str | None] = mapped_column(String(8), nullable=True)
    device_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    has_device: Mapped[bool] = mapped_column(Boolean, default=True)
    firmware: Mapped[str] = mapped_column(String(16), default="1.4.2")

    # Live telemetry state
    energized: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seq: Mapped[int] = mapped_column(Integer, default=0)
    last_event_ts: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    device_offline: Mapped[bool] = mapped_column(Boolean, default=False)  # suspected dead modem
    sensor_suspect: Mapped[bool] = mapped_column(Boolean, default=False)  # isolated dark, live children

    dt: Mapped["DistributionTransformer"] = relationship(back_populates="poles")


class ScheduledOutage(Base):
    __tablename__ = "scheduled_outages"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope: Mapped[str] = mapped_column(String(16))  # feeder | dt
    target_id: Mapped[str] = mapped_column(String(32), index=True)
    start: Mapped[datetime] = mapped_column(DateTime)
    end: Mapped[datetime] = mapped_column(DateTime)
    reason: Mapped[str] = mapped_column(String(256))
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default=TicketStatus.DETECTED.value, index=True)
    fault_type: Mapped[str] = mapped_column(String(16))
    feeder_id: Mapped[str] = mapped_column(String(32), index=True)
    dt_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # Span endpoints (upstream live-ish, downstream dark)
    upstream_pole_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    downstream_pole_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    asset_label: Mapped[str] = mapped_column(String(128))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    pincode: Mapped[str | None] = mapped_column(String(8), nullable=True)
    affected_pole_count: Mapped[int] = mapped_column(Integer, default=0)
    households_estimate: Mapped[int] = mapped_column(Integer, default=0)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    confidence_reason: Mapped[str] = mapped_column(Text, default="")
    topology_source: Mapped[str] = mapped_column(String(16))
    affected_pole_ids: Mapped[str] = mapped_column(Text, default="[]")  # JSON list
    grouping_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    operator_notes: Mapped[str] = mapped_column(Text, default="")
    crew_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ai_briefing: Mapped[str | None] = mapped_column(Text, nullable=True)
    false_resolve_attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TelemetryEvent(Base):
    __tablename__ = "telemetry_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(64), index=True)
    pole_id: Mapped[str] = mapped_column(String(32), index=True)
    event: Mapped[str] = mapped_column(String(32))
    energized: Mapped[bool] = mapped_column(Boolean)
    ts: Mapped[datetime] = mapped_column(DateTime)
    seq: Mapped[int] = mapped_column(Integer)
    battery_mv: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rssi: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fw: Mapped[str | None] = mapped_column(String(16), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    accepted: Mapped[bool] = mapped_column(Boolean, default=True)
    reject_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)


class SimulatorState(Base):
    __tablename__ = "simulator_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    active_faults_json: Mapped[str] = mapped_column(Text, default="[]")
    dead_devices_json: Mapped[str] = mapped_column(Text, default="[]")
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


_DB_URL = settings.resolved_database_url()

engine = create_engine(
    _DB_URL,
    connect_args={"check_same_thread": False} if _DB_URL.startswith("sqlite") else {},
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    if _DB_URL.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
