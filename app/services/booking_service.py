"""Booking engine: create, cancel, reschedule and look up appointments.

Concurrency model (see also the triggers in app/models/appointment.py):

  * Every write runs inside `_write_lock`, so within one process "check availability, then
    insert" is atomic - two patients confirming the same slot are serialised and the second
    one is told the slot was just booked.
  * Across processes the SQLite triggers reject any overlapping CONFIRMED interval; that
    surfaces here as an IntegrityError which we translate to SlotUnavailableError.

Ownership: every mutation takes the caller's phone number and only ever touches appointments
belonging to that number. A mismatch is reported as "not found" so we never confirm that
somebody else's booking exists.
"""

import logging
import threading
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.models import Appointment, AppointmentStatus, Patient
from app.services import availability_service, catalog_service
from app.services.errors import (
    InvalidRequestError,
    InvalidStateError,
    NotFoundError,
    SlotUnavailableError,
)
from app.utils.datetime_utils import now_local
from app.utils.phone_utils import mask_phone, normalize_phone

logger = logging.getLogger(__name__)

BOOKING_REFERENCE_PREFIX = "AST"
BOOKING_REFERENCE_OFFSET = 1000  # first booking is AST-1001

_write_lock = threading.RLock()

_NAME_PUNCTUATION = " .'’-"


def format_booking_reference(appointment_id: int) -> str:
    return f"{BOOKING_REFERENCE_PREFIX}-{BOOKING_REFERENCE_OFFSET + appointment_id}"


def clean_patient_name(raw: str) -> str:
    """Trim/collapse whitespace and validate a patient name (letters, spaces, . ' - only)."""
    name = " ".join(str(raw or "").split())
    if not 2 <= len(name) <= 60:
        raise InvalidRequestError("Patient name must be between 2 and 60 characters.")
    if any(not (char.isalpha() or char in _NAME_PUNCTUATION) for char in name):
        raise InvalidRequestError("Patient name may only contain letters, spaces, . ' and -.")
    if sum(char.isalpha() for char in name) < 2:
        raise InvalidRequestError("Patient name must contain at least two letters.")
    return name


def _normalize_phone_or_raise(phone: str) -> str:
    try:
        return normalize_phone(phone)
    except ValueError as exc:
        raise InvalidRequestError("Invalid phone number.") from exc


def _appointment_query():
    return select(Appointment).options(
        joinedload(Appointment.patient),
        joinedload(Appointment.doctor),
        joinedload(Appointment.service),
    )


def _get_owned_appointment(db: Session, appointment_id: int, phone: str) -> Appointment:
    """Load an appointment only if it belongs to `phone`; otherwise behave as if it doesn't exist."""
    normalized = _normalize_phone_or_raise(phone)
    appointment = db.scalar(
        _appointment_query().join(Patient).where(Appointment.id == appointment_id, Patient.phone == normalized)
    )
    if appointment is None:
        raise NotFoundError("Appointment not found.")
    return appointment


def _ensure_changeable(appointment: Appointment, now: datetime, action: str) -> None:
    if appointment.status != AppointmentStatus.CONFIRMED:
        raise InvalidStateError(f"This appointment is {appointment.status.value.lower()} and cannot be {action}.")
    if appointment.start_datetime <= now:
        raise InvalidStateError(f"This appointment has already started and cannot be {action}.")


def get_appointment(db: Session, appointment_id: int, *, phone: str) -> Appointment:
    return _get_owned_appointment(db, appointment_id, phone)


def get_patient_appointments(
    db: Session,
    *,
    phone: str,
    upcoming_only: bool = True,
    now: datetime | None = None,
) -> list[Appointment]:
    """A patient's appointments, soonest first. `upcoming_only` = confirmed and not yet started."""
    now = now or now_local()
    normalized = _normalize_phone_or_raise(phone)
    query = _appointment_query().join(Patient).where(Patient.phone == normalized)
    if upcoming_only:
        query = query.where(
            Appointment.status == AppointmentStatus.CONFIRMED, Appointment.start_datetime > now
        ).order_by(Appointment.start_datetime)
    else:
        query = query.order_by(Appointment.start_datetime.desc())
    return list(db.scalars(query))


def create_appointment(
    db: Session,
    *,
    patient_name: str,
    phone: str,
    doctor_id: int,
    service_id: int,
    start: datetime,
    now: datetime | None = None,
) -> Appointment:
    """Book `start` for the patient, after re-checking that the slot is still free."""
    now = now or now_local()
    normalized_phone = _normalize_phone_or_raise(phone)
    name = clean_patient_name(patient_name)

    with _write_lock:
        doctor = catalog_service.get_active_doctor(db, doctor_id)
        service = catalog_service.get_active_service(db, service_id)
        catalog_service.ensure_doctor_offers_service(doctor, service)

        # CHECK AVAILABILITY AGAIN - the slot may have been taken since the patient saw it.
        if not availability_service.is_slot_available(
            db, doctor_id=doctor.id, service_id=service.id, start=start, now=now
        ):
            raise SlotUnavailableError()

        patient = db.scalar(select(Patient).where(Patient.phone == normalized_phone))
        if patient is None:
            patient = Patient(name=name, phone=normalized_phone)
            db.add(patient)
        else:
            patient.name = name  # latest name the patient gave wins

        appointment = Appointment(
            booking_reference=f"PENDING-{uuid.uuid4().hex[:16]}",  # replaced once the id is known
            patient=patient,
            doctor=doctor,
            service=service,
            start_datetime=start,
            end_datetime=start + timedelta(minutes=service.duration_minutes),
            status=AppointmentStatus.CONFIRMED,
            created_at=now,
            updated_at=now,
        )
        db.add(appointment)
        try:
            db.flush()  # assigns the id used for the human-friendly booking reference
            appointment.booking_reference = format_booking_reference(appointment.id)
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            logger.warning("Database rejected overlapping booking for doctor %s at %s", doctor_id, start)
            raise SlotUnavailableError() from exc

    logger.info(
        "Booked %s for %s (doctor %s, %s)",
        appointment.booking_reference,
        mask_phone(normalized_phone),
        doctor_id,
        start,
    )
    return appointment


def cancel_appointment(
    db: Session,
    appointment_id: int,
    *,
    phone: str,
    now: datetime | None = None,
) -> Appointment:
    """Mark the appointment CANCELLED (never deleted); its slot is immediately bookable again."""
    now = now or now_local()
    with _write_lock:
        appointment = _get_owned_appointment(db, appointment_id, phone)
        _ensure_changeable(appointment, now, "cancelled")
        appointment.status = AppointmentStatus.CANCELLED
        appointment.updated_at = now
        db.commit()

    logger.info("Cancelled %s", appointment.booking_reference)
    return appointment


def reschedule_appointment(
    db: Session,
    appointment_id: int,
    *,
    phone: str,
    new_start: datetime,
    now: datetime | None = None,
) -> Appointment:
    """Move the appointment to `new_start` (same doctor and service).

    The existing booking is only updated after the new time is confirmed free - it is never
    cancelled first - so a failed reschedule leaves the patient's current slot untouched.
    """
    now = now or now_local()
    with _write_lock:
        appointment = _get_owned_appointment(db, appointment_id, phone)
        _ensure_changeable(appointment, now, "rescheduled")
        if new_start == appointment.start_datetime:
            raise InvalidRequestError("The new time is the same as the current appointment time.")

        if not availability_service.is_slot_available(
            db,
            doctor_id=appointment.doctor_id,
            service_id=appointment.service_id,
            start=new_start,
            now=now,
            exclude_appointment_id=appointment.id,  # its own current slot doesn't count as a clash
        ):
            raise SlotUnavailableError()

        appointment.start_datetime = new_start
        appointment.end_datetime = new_start + timedelta(minutes=appointment.service.duration_minutes)
        appointment.updated_at = now
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            logger.warning("Database rejected overlapping reschedule of appointment %s", appointment_id)
            raise SlotUnavailableError() from exc

    logger.info("Rescheduled %s to %s", appointment.booking_reference, new_start)
    return appointment
