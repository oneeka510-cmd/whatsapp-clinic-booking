import enum
from datetime import datetime

from sqlalchemy import (
    DDL,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    event,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.database import Base
from app.utils.datetime_utils import now_local


class AppointmentStatus(str, enum.Enum):
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    NO_SHOW = "NO_SHOW"


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (
        CheckConstraint("end_datetime > start_datetime", name="ck_appointments_interval"),
        # Cheap declarative guard against two confirmed bookings starting at the same instant.
        # Overlaps between *different* start times are caught by the triggers below.
        Index(
            "uq_appointments_doctor_start_confirmed",
            "doctor_id",
            "start_datetime",
            unique=True,
            sqlite_where=text("status = 'CONFIRMED'"),
        ),
        Index("ix_appointments_doctor_range", "doctor_id", "start_datetime", "end_datetime"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_reference: Mapped[str] = mapped_column(String(32), unique=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"))
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"))
    # Naive clinic-local datetimes (see app/utils/datetime_utils.py).
    start_datetime: Mapped[datetime] = mapped_column(DateTime)
    end_datetime: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[AppointmentStatus] = mapped_column(
        Enum(AppointmentStatus, native_enum=False, create_constraint=True, length=20),
        default=AppointmentStatus.CONFIRMED,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_local)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_local, onupdate=now_local)

    patient: Mapped["Patient"] = relationship()
    doctor: Mapped["Doctor"] = relationship()
    service: Mapped["Service"] = relationship()


# --- Database-level double-booking protection -------------------------------------------
# The booking service re-checks availability inside a lock before writing, but the database
# is the last line of defence (e.g. several app processes, or a bug elsewhere): these
# triggers make it impossible to store two CONFIRMED appointments whose intervals overlap
# for the same doctor. Intervals are half-open, so back-to-back appointments are allowed.
_OVERLAP_MESSAGE = "Doctor already has a confirmed appointment in this time range"

_TRIGGER_INSERT = f"""
CREATE TRIGGER IF NOT EXISTS trg_appointments_no_overlap_insert
BEFORE INSERT ON appointments
WHEN NEW.status = 'CONFIRMED'
BEGIN
    SELECT RAISE(ABORT, '{_OVERLAP_MESSAGE}')
    WHERE EXISTS (
        SELECT 1 FROM appointments
        WHERE doctor_id = NEW.doctor_id
          AND status = 'CONFIRMED'
          AND start_datetime < NEW.end_datetime
          AND end_datetime > NEW.start_datetime
    );
END;
"""

_TRIGGER_UPDATE = f"""
CREATE TRIGGER IF NOT EXISTS trg_appointments_no_overlap_update
BEFORE UPDATE OF doctor_id, start_datetime, end_datetime, status ON appointments
WHEN NEW.status = 'CONFIRMED'
BEGIN
    SELECT RAISE(ABORT, '{_OVERLAP_MESSAGE}')
    WHERE EXISTS (
        SELECT 1 FROM appointments
        WHERE id != NEW.id
          AND doctor_id = NEW.doctor_id
          AND status = 'CONFIRMED'
          AND start_datetime < NEW.end_datetime
          AND end_datetime > NEW.start_datetime
    );
END;
"""

for _statement in (_TRIGGER_INSERT, _TRIGGER_UPDATE):
    event.listen(Appointment.__table__, "after_create", DDL(_statement).execute_if(dialect="sqlite"))
