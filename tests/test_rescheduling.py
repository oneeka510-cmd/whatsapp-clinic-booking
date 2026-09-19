import pytest

from app.models import Appointment, AppointmentStatus
from app.services import availability_service as availability
from app.services import booking_service as booking
from app.services.errors import (
    InvalidRequestError,
    InvalidStateError,
    NotFoundError,
    SlotUnavailableError,
)
from tests.conftest import (
    DR_SHARMA,
    GENERAL_CONSULTATION,
    MONDAY,
    NOW,
    PHONE_A,
    PHONE_B,
    ROOT_CANAL,
    at,
    hhmm,
)

TUESDAY = MONDAY.replace(day=22)


def book(db, start, *, phone=PHONE_A, service_id=GENERAL_CONSULTATION):
    return booking.create_appointment(
        db,
        patient_name="Rahul Sharma",
        phone=phone,
        doctor_id=DR_SHARMA,
        service_id=service_id,
        start=start,
        now=NOW,
    )


def reschedule(db, appointment, new_start, *, phone=PHONE_A):
    return booking.reschedule_appointment(db, appointment.id, phone=phone, new_start=new_start, now=NOW)


def slots(db, day=MONDAY, service_id=GENERAL_CONSULTATION):
    return hhmm(
        availability.get_available_slots(db, doctor_id=DR_SHARMA, service_id=service_id, day=day, now=NOW)
    )


def test_rescheduling_frees_the_old_slot_and_occupies_the_new_one(db):
    appointment = book(db, at(MONDAY, "17:30"))
    assert "17:30" not in slots(db) and "10:00" in slots(db, TUESDAY)

    updated = reschedule(db, appointment, at(TUESDAY, "10:00"))

    assert updated.id == appointment.id
    assert updated.booking_reference == appointment.booking_reference
    assert updated.status == AppointmentStatus.CONFIRMED
    assert (updated.start_datetime, updated.end_datetime) == (at(TUESDAY, "10:00"), at(TUESDAY, "10:30"))
    assert "17:30" in slots(db)  # old slot is free again
    assert "10:00" not in slots(db, TUESDAY)  # new slot is taken
    assert db.query(Appointment).count() == 1  # updated in place, no duplicate row


def test_reschedule_to_a_taken_slot_fails_and_keeps_the_original_booking(db):
    mine = book(db, at(MONDAY, "17:30"))
    book(db, at(TUESDAY, "10:00"), phone=PHONE_B)

    with pytest.raises(SlotUnavailableError):
        reschedule(db, mine, at(TUESDAY, "10:00"))

    db.refresh(mine)
    assert mine.status == AppointmentStatus.CONFIRMED
    assert mine.start_datetime == at(MONDAY, "17:30")
    assert "17:30" not in slots(db)  # still held by the original booking


def test_reschedule_is_not_a_cancel_then_rebook(db):
    """A failed reschedule must never cancel the existing booking first."""
    mine = book(db, at(MONDAY, "17:30"))
    with pytest.raises(SlotUnavailableError):
        reschedule(db, mine, at(MONDAY, "13:00"))  # not a bookable time at all
    db.refresh(mine)
    assert mine.status == AppointmentStatus.CONFIRMED


def test_can_move_into_a_time_overlapping_your_own_current_slot(db):
    mine = book(db, at(MONDAY, "10:00"), service_id=ROOT_CANAL)  # 10:00-11:00

    updated = reschedule(db, mine, at(MONDAY, "10:30"))  # 10:30-11:30 overlaps only itself

    assert (updated.start_datetime, updated.end_datetime) == (at(MONDAY, "10:30"), at(MONDAY, "11:30"))


def test_reschedule_respects_other_bookings_overlap(db):
    mine = book(db, at(MONDAY, "10:00"), service_id=ROOT_CANAL)  # 10:00-11:00
    book(db, at(MONDAY, "11:30"), phone=PHONE_B)  # 11:30-12:00

    with pytest.raises(SlotUnavailableError):
        reschedule(db, mine, at(MONDAY, "11:00"))  # 11:00-12:00 hits the other booking


def test_cannot_reschedule_someone_elses_appointment(db):
    mine = book(db, at(MONDAY, "17:30"), phone=PHONE_A)
    with pytest.raises(NotFoundError):
        reschedule(db, mine, at(TUESDAY, "10:00"), phone=PHONE_B)


def test_cannot_reschedule_a_cancelled_appointment(db):
    mine = book(db, at(MONDAY, "17:30"))
    booking.cancel_appointment(db, mine.id, phone=PHONE_A, now=NOW)
    with pytest.raises(InvalidStateError):
        reschedule(db, mine, at(TUESDAY, "10:00"))


def test_rescheduling_to_the_same_time_is_rejected(db):
    mine = book(db, at(MONDAY, "17:30"))
    with pytest.raises(InvalidRequestError):
        reschedule(db, mine, at(MONDAY, "17:30"))


def test_cannot_reschedule_an_appointment_that_already_started(db):
    mine = book(db, at(MONDAY, "10:00"))
    with pytest.raises(InvalidStateError):
        booking.reschedule_appointment(
            db, mine.id, phone=PHONE_A, new_start=at(TUESDAY, "10:00"), now=at(MONDAY, "10:05")
        )
