"""SQLAlchemy models for the tables this layer owns."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Station(Base):
    __tablename__ = "stations"
    station_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    source: Mapped[str] = mapped_column(String(16))
    source_station_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str | None] = mapped_column(String(128))
    county: Mapped[str | None] = mapped_column(String(64))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    elevation_ft: Mapped[float | None] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    connect_date: Mapped[datetime | None] = mapped_column(Date)
    disconnect_date: Mapped[datetime | None] = mapped_column(Date)
    in_region: Mapped[bool] = mapped_column(Boolean, default=True)
    tz: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (UniqueConstraint("source", "source_station_id"),)


class Reading(Base):
    __tablename__ = "readings"
    station_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    temp_f: Mapped[float | None] = mapped_column(Float)
    qc_source: Mapped[str | None] = mapped_column(String(16))
    qc_base: Mapped[str] = mapped_column(String(10), default="ok")
    qc_status: Mapped[str] = mapped_column(String(10), default="ok")
    rh_pct: Mapped[float | None] = mapped_column(Float)
    dewpoint_f: Mapped[float | None] = mapped_column(Float)
    solar_wm2: Mapped[float | None] = mapped_column(Float)
    wind_mph: Mapped[float | None] = mapped_column(Float)
    est_temp_f: Mapped[float | None] = mapped_column(Float)
    est_sd: Mapped[float | None] = mapped_column(Float)
    used_temp_f: Mapped[float | None] = mapped_column(Float)
    is_estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    est_method: Mapped[str | None] = mapped_column(String(16))
    raw_ref: Mapped[str | None] = mapped_column(Text)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_readings_ts", "ts"),)


class StationOffset(Base):
    __tablename__ = "station_offsets"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    station_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(16))  # learned | accepted
    night_f: Mapped[float] = mapped_column(Float)
    day_f: Mapped[float] = mapped_column(Float)
    hourly_f: Mapped[str | None] = mapped_column(Text)  # JSON list of 24 PST hour-of-day offsets
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ModelRegistry(Base):
    __tablename__ = "model_registry"
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    path: Mapped[str] = mapped_column(Text)
    metrics: Mapped[dict] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
