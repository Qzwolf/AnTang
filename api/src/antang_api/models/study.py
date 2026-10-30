"""Isolated research sample provenance and timed insulin/meal observations."""
from datetime import datetime
from uuid import UUID
from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from antang_api.database import Base


class StudySubject(Base):
    __tablename__ = 'study_subjects'
    user_id: Mapped[UUID] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    dataset: Mapped[str] = mapped_column(String(64))
    insulin_type: Mapped[str | None] = mapped_column(String(128))
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    provenance: Mapped[dict] = mapped_column(JSONB)


class StudyEvent(Base):
    __tablename__ = 'study_events'
    __table_args__ = (
        CheckConstraint("kind IN ('bolus','basal','temp_basal','meal','finger_stick')", name='ck_study_event_kind'),
        CheckConstraint('value >= 0', name='ck_study_event_value'),
        CheckConstraint('ended_at IS NULL OR ended_at >= measured_at', name='ck_study_event_time'),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(16))
    detail: Mapped[str] = mapped_column(String(128))
    source_ref: Mapped[str] = mapped_column(String(255))
    raw_record: Mapped[dict] = mapped_column(JSONB)
