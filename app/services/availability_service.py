"""The availability engine.

This is the single source of truth for "can this time be booked?". The booking service
does not carry its own rules: it simply asks `is_slot_available`, which means the slots we
*show* and the slots we *accept* can never disagree.

Slot generation, for one doctor and one service on one day:

  1. Take the doctor's schedule blocks for that weekday (none => closed => no slots).
  2. Walk each block on a fixed grid (SLOT_INTERVAL_MINUTES) and keep the start times where
     the *whole* service duration still fits inside the block.
  3. Drop any candidate interval that overlaps a CONFIRMED appointment of that doctor,
     using half-open interval overlap:  existing.start < candidate.end AND existing.end > candidate.start.
     (so a 60-minute service can't start at 10:00 if something occupies 10:30-11:00, and
     back-to-back appointments are fine).
  4. Drop anything that has already started, and days outside the booking window.

All datetimes are naive clinic-local time.
"""

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Appointment, AppointmentStatus, Doctor, DoctorSchedule, Service
from app.services import catalog_service
from app.utils.datetime_utils import day_bounds, now_local

logger = logging.getLogger(__name__)


def _slots_for_doctor(
    db: Session,
    doctor: Doctor,
    service: Service,
    day: date,
    now: datetime,
    exclude_appointment_id: int | None,
) -> list[datetime]:
    settings = get_settings()
    today = now.date()
    if day < today or day > today + timedelta(days=settings.booking_window_days):
        return []

    blocks = db.scalars(
        select(DoctorSchedule)
        .where(DoctorSchedule.doctor_id == doctor.id, DoctorSchedule.day_of_week == day.weekday())
        .order_by(DoctorSchedule.start_time)
    ).all()
    if not blocks:
        return []

    window_start, window_end = day_bounds(day)
    booked_query = select(Appointment.start_datetime, Appointment.end_datetime).where(
        Appointment.doctor_id == doctor.id,
        Appointment.status == AppointmentStatus.CONFIRMED,
        Appointment.start_datetime < window_end,
        Appointment.end_datetime > window_start,
    )
    if exclude_appointment_id is not None:
        booked_query = booked_query.where(Appointment.id != exclude_appointment_id)
    booked = db.execute(booked_query).all()

    duration = timedelta(minutes=service.duration_minutes)
    step = timedelta(minutes=settings.slot_interval_minutes)

    slots: set[datetime] = set()
    for block in blocks:
        cursor = datetime.combine(day, block.start_time)
        block_end = datetime.combine(day, block.end_time)
        while cursor + duration <= block_end:
            slot_end = cursor + duration
            is_future = cursor > now
            overlaps = any(start < slot_end and end > cursor for start, end in booked)
            if is_future and not overlaps:
                slots.add(cursor)
            cursor += step
    return sorted(slots)


def get_slot_doctors(
    db: Session,
    *,
    service_id: int,
    day: date,
    doctor_id: int | None = None,
    now: datetime | None = None,
    exclude_appointment_id: int | None = None,
) -> dict[datetime, list[int]]:
    """Map each bookable start time to the ids of the doctors free at that time (ordered by id).

    With `doctor_id` the map covers that doctor only; without it, every active doctor who
    performs the service ("Any Available Doctor").
    """
    now = now or now_local()
    service = catalog_service.get_active_service(db, service_id)

    if doctor_id is not None:
        doctor = catalog_service.get_active_doctor(db, doctor_id)
        catalog_service.ensure_doctor_offers_service(doctor, service)
        doctors = [doctor]
    else:
        doctors = catalog_service.list_doctors(db, service_id=service_id)

    by_slot: dict[datetime, list[int]] = defaultdict(list)
    for doctor in doctors:
        for slot in _slots_for_doctor(db, doctor, service, day, now, exclude_appointment_id):
            by_slot[slot].append(doctor.id)
    return dict(sorted(by_slot.items()))


def get_available_slots(
    db: Session,
    *,
    doctor_id: int,
    service_id: int,
    day: date,
    now: datetime | None = None,
    exclude_appointment_id: int | None = None,
) -> list[datetime]:
    """Start times a given doctor can take `service_id` on `day`.

    `exclude_appointment_id` ignores one existing appointment, so that a patient
    rescheduling can move to a time that overlaps their own current slot.
    """
    slot_map = get_slot_doctors(
        db,
        service_id=service_id,
        day=day,
        doctor_id=doctor_id,
        now=now,
        exclude_appointment_id=exclude_appointment_id,
    )
    return list(slot_map)


def is_slot_available(
    db: Session,
    *,
    doctor_id: int,
    service_id: int,
    start: datetime,
    now: datetime | None = None,
    exclude_appointment_id: int | None = None,
) -> bool:
    """True only if `start` is one of the slots `get_available_slots` would currently offer."""
    slots = get_available_slots(
        db,
        doctor_id=doctor_id,
        service_id=service_id,
        day=start.date(),
        now=now,
        exclude_appointment_id=exclude_appointment_id,
    )
    return start in slots


def get_bookable_dates(
    db: Session,
    *,
    service_id: int,
    doctor_id: int | None = None,
    from_day: date | None = None,
    limit: int = 10,
    now: datetime | None = None,
    exclude_appointment_id: int | None = None,
) -> list[tuple[date, int]]:
    """The next `limit` days (from `from_day`) that have at least one slot, with the slot count."""
    now = now or now_local()
    first_day = from_day or now.date()
    last_day = now.date() + timedelta(days=get_settings().booking_window_days)

    results: list[tuple[date, int]] = []
    day = first_day
    while day <= last_day and len(results) < limit:
        slots = get_slot_doctors(
            db,
            service_id=service_id,
            day=day,
            doctor_id=doctor_id,
            now=now,
            exclude_appointment_id=exclude_appointment_id,
        )
        if slots:
            results.append((day, len(slots)))
        day += timedelta(days=1)
    return results
