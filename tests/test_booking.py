from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.database import Base, make_engine
from app.database.seed import seed_database
from app.models import Appointment, AppointmentStatus, Patient
from app.services import availability_service as availability
from app.services import booking_service as booking
from app.services.errors import (
    InvalidRequestError,
    InvalidStateError,
    NotFoundError,
    SlotUnavailableError,
)
from tests.conftest import (
    DR_MEHTA,
    DR_SHARMA,
    GENERAL_CONSULTATION,
    MONDAY,
    NOW,
    PHONE_A,
    PHONE_B,
    ROOT_CANAL,
    at,
    hhmm,
    insert_appointment,
)


def book(db, start, *, name="Rahul Sharma", phone=PHONE_A, doctor_id=DR_SHARMA, service_id=GENERAL_CONSULTATION):
    return booking.create_appointment(
        db, patient_name=name, phone=phone, doctor_id=doctor_id, service_id=service_id, start=start, now=NOW
    )


def slots(db, doctor_id=DR_SHARMA, service_id=GENERAL_CONSULTATION):
    return hhmm(
        availability.get_available_slots(db, doctor_id=doctor_id, service_id=service_id, day=MONDAY, now=NOW)
    )


# --- creating -------------------------------------------------------------------------


def test_create_appointment_stores_a_confirmed_booking(db):
    appointment = book(db, at(MONDAY, "17:30"))

    assert appointment.booking_reference == f"AST-{1000 + appointment.id}"
    assert appointment.booking_reference == "AST-1001"
    assert appointment.status == AppointmentStatus.CONFIRMED
    assert appointment.start_datetime == at(MONDAY, "17:30")
    assert appointment.end_datetime == at(MONDAY, "18:00")
    assert appointment.patient.name == "Rahul Sharma"
    assert appointment.patient.phone == PHONE_A


def test_end_time_uses_the_service_duration(db):
    appointment = book(db, at(MONDAY, "10:00"), service_id=ROOT_CANAL)
    assert appointment.end_datetime == at(MONDAY, "11:00")


def test_booking_references_are_unique_and_sequential(db):
    first = book(db, at(MONDAY, "09:00"))
    second = book(db, at(MONDAY, "09:30"))
    assert (first.booking_reference, second.booking_reference) == ("AST-1001", "AST-1002")


def test_booked_slot_disappears_from_availability(db):
    assert "17:30" in slots(db)
    book(db, at(MONDAY, "17:30"))
    assert "17:30" not in slots(db)


def test_patient_is_reused_by_normalized_phone_number(db):
    book(db, at(MONDAY, "09:00"), phone="+91 98765 43210")
    book(db, at(MONDAY, "09:30"), phone="919876543210", name="Rahul K. Sharma")
    book(db, at(MONDAY, "10:00"), phone="9876543210", name="Rahul K. Sharma")

    patients = db.query(Patient).filter(Patient.phone == PHONE_A).all()
    assert len(patients) == 1
    assert patients[0].name == "Rahul K. Sharma"  # latest name wins


@pytest.mark.parametrize("phone", ["", "abc", "12345", "+1234567890123456789"])
def test_invalid_phone_numbers_are_rejected(db, phone):
    with pytest.raises(InvalidRequestError):
        book(db, at(MONDAY, "09:00"), phone=phone)


@pytest.mark.parametrize("name", ["", " ", "A", "R2D2", "Rahul <script>", "x" * 61, "-- --"])
def test_invalid_patient_names_are_rejected(db, name):
    with pytest.raises(InvalidRequestError):
        book(db, at(MONDAY, "09:00"), name=name)


@pytest.mark.parametrize("name", ["Rahul Sharma", "Mary-Jane O'Brien", "Dr. A. P. J. Kalam", "José Núñez"])
def test_reasonable_names_are_accepted(name):
    assert booking.clean_patient_name(f"  {name}  ") == name


def test_cannot_book_a_slot_the_doctor_does_not_offer(db):
    with pytest.raises(SlotUnavailableError):
        book(db, at(MONDAY, "13:00"))  # lunch break
    with pytest.raises(SlotUnavailableError):
        book(db, at(MONDAY, "10:15"))  # off the slot grid
    with pytest.raises(SlotUnavailableError):
        book(db, at(NOW.date(), "09:00"))  # already started


def test_cannot_book_with_a_doctor_who_does_not_perform_the_service(db):
    with pytest.raises(InvalidRequestError):
        book(db, at(MONDAY, "10:00"), doctor_id=DR_MEHTA, service_id=ROOT_CANAL)


# --- double booking -------------------------------------------------------------------


def test_same_slot_cannot_be_booked_twice(db):
    book(db, at(MONDAY, "17:30"))

    with pytest.raises(SlotUnavailableError):
        book(db, at(MONDAY, "17:30"), phone=PHONE_B, name="Second Patient")

    assert db.query(Appointment).count() == 1


def test_overlapping_intervals_cannot_be_booked(db):
    book(db, at(MONDAY, "10:30"))  # 10:30-11:00

    with pytest.raises(SlotUnavailableError):  # 10:00-11:00 overlaps it
        book(db, at(MONDAY, "10:00"), service_id=ROOT_CANAL, phone=PHONE_B)
    with pytest.raises(SlotUnavailableError):  # 10:30-11:30 overlaps it
        book(db, at(MONDAY, "10:30"), service_id=ROOT_CANAL, phone=PHONE_B)


def test_back_to_back_appointments_are_allowed(db):
    book(db, at(MONDAY, "10:30"))
    book(db, at(MONDAY, "10:00"), phone=PHONE_B)  # ends exactly when the first starts
    book(db, at(MONDAY, "11:00"), phone=PHONE_B)  # starts exactly when the first ends
    assert db.query(Appointment).count() == 3


def test_same_time_with_different_doctors_is_fine(db):
    book(db, at(MONDAY, "10:00"), doctor_id=DR_SHARMA)
    book(db, at(MONDAY, "10:00"), doctor_id=DR_MEHTA, phone=PHONE_B)
    assert db.query(Appointment).count() == 2


def test_database_itself_rejects_overlapping_confirmed_appointments(db):
    """Even bypassing the booking service, the DB never stores two overlapping confirmed rows."""
    insert_appointment(db, start=at(MONDAY, "10:00"), minutes=60)  # 10:00-11:00

    with pytest.raises(IntegrityError):
        insert_appointment(db, start=at(MONDAY, "10:30"))  # 10:30-11:00 sits inside it
    db.rollback()

    with pytest.raises(IntegrityError):
        insert_appointment(db, start=at(MONDAY, "09:30"), minutes=60)  # 09:30-10:30 overlaps its start
    db.rollback()

    insert_appointment(db, start=at(MONDAY, "11:00"))  # touching is fine
    insert_appointment(db, start=at(MONDAY, "10:00"), doctor_id=DR_MEHTA)  # other doctor is fine
    # A cancelled row never conflicts, and neither does re-confirming into a free slot.
    insert_appointment(db, start=at(MONDAY, "10:00"), status=AppointmentStatus.CANCELLED)
    assert db.query(Appointment).count() == 4


def test_database_rejects_an_update_that_creates_an_overlap(db):
    insert_appointment(db, start=at(MONDAY, "10:00"))
    other = insert_appointment(db, start=at(MONDAY, "12:00"))

    other.start_datetime, other.end_datetime = at(MONDAY, "10:00"), at(MONDAY, "10:30")
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_concurrent_confirmations_only_one_wins(tmp_path):
    """Many patients confirm the same slot at once (real threads, real file database)."""
    engine = make_engine(f"sqlite:///{tmp_path / 'race.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed_database(session)

    def attempt(index: int) -> str:
        with Session(engine) as session:
            try:
                booking.create_appointment(
                    session,
                    patient_name=f"Patient {chr(ord('A') + index)}",  # names cannot contain digits
                    phone=f"+9198765432{index:02d}",
                    doctor_id=DR_SHARMA,
                    service_id=GENERAL_CONSULTATION,
                    start=at(MONDAY, "17:30"),
                    now=NOW,
                )
                return "booked"
            except SlotUnavailableError:
                return "unavailable"

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(attempt, range(12)))

    assert results.count("booked") == 1
    assert results.count("unavailable") == 11
    with Session(engine) as session:
        assert session.query(Appointment).filter_by(status=AppointmentStatus.CONFIRMED).count() == 1
    engine.dispose()


# --- cancelling -----------------------------------------------------------------------


def test_cancelled_slot_becomes_available_again(db):
    appointment = book(db, at(MONDAY, "17:30"))
    assert "17:30" not in slots(db)

    cancelled = booking.cancel_appointment(db, appointment.id, phone=PHONE_A, now=NOW)

    assert cancelled.status == AppointmentStatus.CANCELLED
    assert "17:30" in slots(db)
    # ...and someone else can now take it.
    book(db, at(MONDAY, "17:30"), phone=PHONE_B, name="Second Patient")


def test_cancelling_never_deletes_the_row(db):
    appointment = book(db, at(MONDAY, "17:30"))
    booking.cancel_appointment(db, appointment.id, phone=PHONE_A, now=NOW)

    stored = db.get(Appointment, appointment.id)
    assert stored is not None and stored.status == AppointmentStatus.CANCELLED


def test_cannot_cancel_someone_elses_appointment(db):
    appointment = book(db, at(MONDAY, "17:30"), phone=PHONE_A)

    with pytest.raises(NotFoundError):
        booking.cancel_appointment(db, appointment.id, phone=PHONE_B, now=NOW)

    assert db.get(Appointment, appointment.id).status == AppointmentStatus.CONFIRMED


def test_cannot_cancel_twice_or_cancel_unknown_ids(db):
    appointment = book(db, at(MONDAY, "17:30"))
    booking.cancel_appointment(db, appointment.id, phone=PHONE_A, now=NOW)

    with pytest.raises(InvalidStateError):
        booking.cancel_appointment(db, appointment.id, phone=PHONE_A, now=NOW)
    with pytest.raises(NotFoundError):
        booking.cancel_appointment(db, 9999, phone=PHONE_A, now=NOW)


def test_cannot_cancel_an_appointment_that_already_started(db):
    appointment = book(db, at(MONDAY, "10:00"))
    later = at(MONDAY, "10:15")

    with pytest.raises(InvalidStateError):
        booking.cancel_appointment(db, appointment.id, phone=PHONE_A, now=later)


# --- looking up -----------------------------------------------------------------------


def test_patient_appointments_are_scoped_to_the_phone_and_sorted(db):
    later = book(db, at(MONDAY, "15:00"))
    sooner = book(db, at(MONDAY, "09:00"))
    book(db, at(MONDAY, "11:00"), phone=PHONE_B, name="Someone Else")
    cancelled = book(db, at(MONDAY, "12:00"))
    booking.cancel_appointment(db, cancelled.id, phone=PHONE_A, now=NOW)

    mine = booking.get_patient_appointments(db, phone="+91 98765-43210", now=NOW)

    assert [a.id for a in mine] == [sooner.id, later.id]  # only mine, only confirmed, soonest first


def test_past_appointments_are_not_upcoming(db):
    appointment = book(db, at(MONDAY, "09:00"))
    after = at(MONDAY, "09:01")

    assert booking.get_patient_appointments(db, phone=PHONE_A, now=after) == []
    history = booking.get_patient_appointments(db, phone=PHONE_A, upcoming_only=False, now=after)
    assert [a.id for a in history] == [appointment.id]


def test_get_appointment_enforces_ownership(db):
    appointment = book(db, at(MONDAY, "09:00"), phone=PHONE_A)
    assert booking.get_appointment(db, appointment.id, phone=PHONE_A).id == appointment.id
    with pytest.raises(NotFoundError):
        booking.get_appointment(db, appointment.id, phone=PHONE_B)
